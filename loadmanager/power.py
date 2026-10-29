"""Quiet mode: make the laptop quieter while you work, and put everything back afterwards.

Two ways, chosen on the Power page:
  plan   switch the Windows power plan (Silent, Balanced ... or the one you pick);
  limit  leave your plan alone and only change two processor settings in it: turn turbo boost off and/or lower
         the maximum processor state. Sleep, display and every other setting of your plan stay as they are.

Whatever was changed is written to the recovery journal before it is changed, so the guardian process and the
next start can put it back after a crash. A change made by anything else (you, the keyboard shortcut, the laptop
vendor's software) is never fought: quiet mode steps aside and leaves it. While quiet mode is on and work is
running, the laptop is also kept from going to sleep on its own.

Measured on an ASUS ROG Zephyrus G14 (Ryzen AI 9 HX 370): turning boost off cut the busiest core from about 250%
to 101% of the base clock; the maximum processor state, the maximum frequency and the energy preference had no
effect at all. Other processors may behave differently, so both settings are offered."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import threading
import time

from .engine import FileLock, PolicyError, read_json

_GUID = r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}"
_LINE = re.compile(rf"({_GUID})\s+\((.*?)\)(\s*\*)?\s*$")
_ONLY_GUID = re.compile(rf"^{_GUID}$")
_ALIAS = re.compile(r"^[A-Z0-9_]{3,40}$")
_HEX_LAST = re.compile(r"0x([0-9a-fA-F]{8})\s*$")
SUB_PROCESSOR = "SUB_PROCESSOR"
BOOST, MAX_STATE = "PERFBOOSTMODE", "PROCTHROTTLEMAX"
BOOST_NAMES = {0: "Disabled", 1: "Enabled", 2: "Aggressive", 3: "Efficient Enabled", 4: "Efficient Aggressive",
               5: "Aggressive At Guaranteed", 6: "Efficient Aggressive At Guaranteed"}
CAP_MIN, CAP_MAX = 30, 100          # 100 = leave the maximum processor state alone


class Powercfg:
    """Thin wrapper around the powercfg command. Output is parsed by GUID and by position, not by words, so it
    works on any display language."""
    def __init__(self, run=None):
        self._run = run or self._default_run

    @staticmethod
    def available():
        return os.name == "nt" and shutil.which("powercfg") is not None

    @staticmethod
    def _default_run(args):
        return subprocess.run(args, capture_output=True, text=True, errors="replace", timeout=10,
                              creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)

    def _call(self, *args):
        try:
            result = self._run(["powercfg", *args])
        except (OSError, subprocess.SubprocessError) as exc:
            raise OSError(f"powercfg failed: {type(exc).__name__}") from exc
        if result.returncode != 0:
            raise OSError(f"powercfg {args[0]} returned {result.returncode}")
        return result.stdout or ""

    @staticmethod
    def _parse(text):
        found = []
        for line in text.splitlines():
            match = _LINE.search(line.strip())
            if match:
                found.append({"guid": match.group(1).lower(), "name": match.group(2), "active": bool(match.group(3))})
        return found

    def schemes(self):
        return self._parse(self._call("/list"))

    def active(self):
        found = self._parse(self._call("/getactivescheme"))
        if not found:
            raise OSError("Could not read the active power plan")
        return {"guid": found[0]["guid"], "name": found[0]["name"]}

    def activate(self, guid):
        if not _ONLY_GUID.match(guid or ""):
            raise OSError("Invalid power plan id")
        self._call("/setactive", guid)

    @staticmethod
    def _check(guid, sub, setting):
        if not (_ONLY_GUID.match(guid or "") and (_ALIAS.match(sub or "") or _ONLY_GUID.match(sub or ""))
                and (_ALIAS.match(setting or "") or _ONLY_GUID.match(setting or ""))):
            raise OSError("Invalid power setting")

    def value(self, guid, sub, setting):
        """The plan's AC and DC value of one setting. They are the last two numbers printed (AC first).
        /qh also lists the hidden settings (turbo boost is one of them)."""
        self._check(guid, sub, setting)
        numbers = []
        for line in self._call("/qh", guid, sub, setting).splitlines():
            match = _HEX_LAST.search(line.strip())
            if match:
                numbers.append(int(match.group(1), 16))
        if len(numbers) < 2:
            return {"ac": None, "dc": None}
        return {"ac": numbers[-2], "dc": numbers[-1]}

    def set_value(self, guid, sub, setting, ac=None, dc=None):
        self._check(guid, sub, setting)
        for flag, number in (("/setacvalueindex", ac), ("/setdcvalueindex", dc)):
            if number is None:
                continue
            if isinstance(number, bool) or not isinstance(number, int) or not 0 <= number <= 100000:
                raise OSError("Invalid power setting value")
            self._call(flag, guid, sub, setting, str(number))


class PowerGuard:
    CHECK_EVERY = 10.0       # seconds between checks that nobody else changed the plan or the settings
    REFRESH_EVERY = 30.0     # seconds between reads of the plan list
    WAKE_GRACE = 60.0        # keep the laptop awake this long after the last sign of work
    QUIET_NAMES = ("silent", "quiet", "power saver", "battery", "balanced", "eco")
    PLAN_KEYS = {"before", "before_name", "after", "after_name", "now", "since"}
    LIMIT_KEYS = {"mode", "scheme", "scheme_name", "changes", "since"}
    CHANGE_KEYS = {"alias", "label", "before_ac", "before_dc", "after", "now"}

    def __init__(self, session, powercfg=None, wake=None):
        self.session = session
        self.cfg = powercfg
        self.wake = wake
        self.lock = threading.RLock()
        saved = read_json(session.settings_path, {}).get("quiet", {})
        guid = lambda value: value.lower() if isinstance(value, str) and _ONLY_GUID.match(value) else None
        self.scheme = guid(saved.get("scheme"))
        self.render_scheme = guid(saved.get("render_scheme"))
        self.auto_enter = bool(saved.get("auto_enter", False))
        self.mode = saved.get("mode") if saved.get("mode") in ("plan", "limit") else "plan"
        cap = saved.get("cap")
        self.cap = cap if isinstance(cap, int) and not isinstance(cap, bool) and CAP_MIN <= cap <= CAP_MAX else CAP_MAX
        self.boost_off = bool(saved.get("boost_off", True))
        self.keep_awake = bool(saved.get("keep_awake", True))
        self.entry = None            # journaled, see PLAN_KEYS / LIMIT_KEYS
        self.holds = 0               # renders in progress that want the original/performance setting
        self.note = None
        self.error = None
        self.cache = {"schemes": [], "active": None, "at": 0.0}
        self.checked_at = 0.0
        self.busy_at = None
        self.wake_error = None

    @classmethod
    def valid_entry(cls, entry):
        """Journal entries from older versions have no "mode" and are plan switches."""
        if not isinstance(entry, dict):
            return False
        if entry.get("mode") == "limit":
            changes = entry.get("changes")
            return (cls.LIMIT_KEYS <= set(entry) and isinstance(changes, list)
                    and all(isinstance(c, dict) and cls.CHANGE_KEYS <= set(c) for c in changes))
        return cls.PLAN_KEYS <= set(entry)

    # -- reading ------------------------------------------------------------------------------

    @property
    def available(self):
        return self.cfg is not None

    def refresh(self, force=False, now=None):
        if self.cfg is None:
            return
        now = time.time() if now is None else now
        if not force and now - self.cache["at"] < self.REFRESH_EVERY:
            return
        try:
            schemes, active = self.cfg.schemes(), self.cfg.active()
        except OSError as exc:
            with self.lock:
                self.error = str(exc)
            return
        with self.lock:
            self.cache = {"schemes": schemes, "active": active, "at": now}
            self.error = None

    def _find(self, guid):
        return next((s for s in self.cache["schemes"] if s["guid"] == guid), None)

    def _quiet_target(self):
        """The chosen plan, else the first match of Silent, Quiet, Power saver, Balanced..."""
        chosen = self._find(self.scheme) if self.scheme else None
        if chosen:
            return chosen
        for word in self.QUIET_NAMES:
            for scheme in self.cache["schemes"]:
                if word in scheme["name"].lower():
                    return scheme
        return None

    def _render_target(self):
        chosen = self._find(self.render_scheme) if self.render_scheme else None
        return chosen["guid"] if chosen else self.entry["before"]

    @staticmethod
    def _describe(change, value):
        if change["alias"] == BOOST:
            return "Off" if value == 0 else BOOST_NAMES.get(value, str(value))
        return f"{value}%"

    def state(self):
        with self.lock:
            entry = dict(self.entry) if self.entry else None
            limit = entry is not None and entry.get("mode") == "limit"
            original = None
            if entry:
                original = ({"guid": entry["scheme"], "name": entry["scheme_name"]} if limit
                            else {"guid": entry["before"], "name": entry["before_name"]})
            limits = None
            if limit:
                limits = [{"label": c["label"], "before": self._describe(c, c["before_ac"]),
                           "now": self._describe(c, c["now"])} for c in entry["changes"]]
            return {"available": self.available, "active": entry is not None, "mode": self.mode, "cap": self.cap,
                    "boost_off": self.boost_off, "state": ("render" if self.holds else "quiet") if entry else "off",
                    "scheme": self.scheme, "render_scheme": self.render_scheme,
                    "quiet_target": self._quiet_target(), "original": original, "limits": limits,
                    "current": self.cache["active"], "schemes": list(self.cache["schemes"]),
                    "auto_enter": self.auto_enter, "keep_awake": self.keep_awake,
                    "awake": bool(self.wake is not None and self.wake.held),
                    "since": entry["since"] if entry else None,
                    "note": self.note, "error": self.error or self.wake_error}

    # -- configuration --------------------------------------------------------------------------

    def configure(self, scheme=None, render_scheme=None, auto_enter=None, mode=None, cap=None, keep_awake=None,
                  boost_off=None):
        """scheme / render_scheme: a plan id, or "" for the automatic choice."""
        with self.session.mutex:
            if self.entry and any(v is not None for v in (scheme, render_scheme, mode, cap, boost_off)):
                raise PolicyError("Leave quiet mode before changing these settings")
            self.refresh(force=True)
            for value in (scheme, render_scheme):
                if value not in (None, "") and (not isinstance(value, str) or not self._find(value.lower())):
                    raise PolicyError("That power plan does not exist on this computer")
            if mode is not None and mode not in ("plan", "limit"):
                raise PolicyError("Unknown quiet mode method")
            if cap is not None and (isinstance(cap, bool) or not isinstance(cap, int) or not CAP_MIN <= cap <= CAP_MAX):
                raise PolicyError(f"The maximum processor state must be a whole number from {CAP_MIN} to {CAP_MAX} percent")
            if scheme is not None:
                self.scheme = scheme.lower() or None
            if render_scheme is not None:
                self.render_scheme = render_scheme.lower() or None
            if auto_enter is not None:
                self.auto_enter = bool(auto_enter)
            if mode is not None:
                self.mode = mode
            if cap is not None:
                self.cap = cap
            if boost_off is not None:
                self.boost_off = bool(boost_off)
            if keep_awake is not None:
                self.keep_awake = bool(keep_awake)
            self.session.update_settings("quiet", {"scheme": self.scheme, "render_scheme": self.render_scheme,
                                                   "auto_enter": self.auto_enter, "mode": self.mode, "cap": self.cap,
                                                   "boost_off": self.boost_off, "keep_awake": self.keep_awake})
            return self.state()

    # -- entering and leaving -----------------------------------------------------------------------

    def enter(self):
        if self.cfg is None:
            raise PolicyError("Quiet mode needs Windows power plans")
        with self.session.mutex, FileLock(self.session.lock_path):
            if self.entry:
                return self.state()
            self.refresh(force=True)
            if self.cache["active"] is None:
                raise PolicyError(self.error or "Could not read the active power plan")
            return self._enter_limit_locked() if self.mode == "limit" else self._enter_plan_locked()

    def _enter_plan_locked(self):
        active, target = self.cache["active"], self._quiet_target()
        if target is None:
            raise PolicyError("No quiet power plan found; choose one on the Power page")
        if target["guid"] == active["guid"]:
            raise PolicyError(f"\"{target['name']}\" is already the active power plan")
        self.entry = {"mode": "plan", "before": active["guid"], "before_name": active["name"],
                      "after": target["guid"], "after_name": target["name"], "now": active["guid"], "since": time.time()}
        self.session._save()                                  # intent first, as with every other change
        goal = self._render_target() if self.holds else target["guid"]
        try:
            self._activate_locked(goal)
        except OSError as exc:
            self.entry = None
            self.session._save()
            raise PolicyError(f"Could not switch the power plan: {exc}") from exc
        self.note, self.checked_at = None, time.time()
        self.session.log(f"Quiet mode: power plan {active['name']} → {(self._find(goal) or {}).get('name', goal)}", "action")
        return self.state()

    def _plan_changes(self, guid, name):
        """What to change in this plan: only settings that would actually become lower."""
        wanted = []
        if self.boost_off:
            wanted.append((BOOST, "Turbo boost", 0))
        if self.cap < CAP_MAX:
            wanted.append((MAX_STATE, "Maximum processor state", self.cap))
        if not wanted:
            raise PolicyError("Nothing to limit: turn turbo boost off or set a maximum state below 100%")
        changes = []
        try:
            for alias, label, after in wanted:
                v = self.cfg.value(guid, SUB_PROCESSOR, alias)
                if v["ac"] is None:
                    continue                                  # this plan does not have the setting
                dc = v["dc"] if v["dc"] is not None else v["ac"]
                if v["ac"] > after or dc > after:
                    changes.append({"alias": alias, "label": label, "before_ac": v["ac"], "before_dc": dc,
                                    "after": after, "now": v["ac"]})
        except OSError as exc:
            raise PolicyError(f"Could not read the processor settings: {exc}") from exc
        if not changes:
            raise PolicyError(f"Nothing to limit in the plan \"{name}\": it is already at or below the chosen limits, "
                              "or has no such settings")
        return changes

    def _enter_limit_locked(self):
        active = self.cache["active"]
        self.entry = {"mode": "limit", "scheme": active["guid"], "scheme_name": active["name"],
                      "changes": self._plan_changes(active["guid"], active["name"]), "since": time.time()}
        self.session._save()
        try:
            self._set_limits_locked(render=bool(self.holds))
        except OSError as exc:
            self._undo_limits_quietly()
            raise PolicyError(f"Could not change the processor settings: {exc}") from exc
        self.note, self.checked_at = None, time.time()
        self.session.log("Quiet mode: " + "; ".join(f"{c['label']} {self._describe(c, c['before_ac'])} → "
                                                    f"{self._describe(c, c['now'])}" for c in self.entry["changes"])
                         + f" in {active['name']}", "action")
        return self.state()

    def _undo_limits_quietly(self):
        """After a failed first change: try to put the old values back, then forget the entry."""
        entry = self.entry
        for c in entry["changes"]:
            try:
                self.cfg.set_value(entry["scheme"], SUB_PROCESSOR, c["alias"], ac=c["before_ac"], dc=c["before_dc"])
            except OSError:
                pass
        self.entry = None
        self.session._save()

    def leave(self):
        with self.session.mutex, FileLock(self.session.lock_path):
            if self.entry:
                self._revert_locked()
                self.session._save()
            self._update_wake(time.time())
            return self.state()

    def _activate_locked(self, guid):
        self.cfg.activate(guid)
        self.entry["now"] = guid
        self.session._save()
        self.cache["active"] = {"guid": guid, "name": (self._find(guid) or {}).get("name", guid)}

    def _set_limits_locked(self, render):
        """Quiet values, or the original ones for a render. A lower value that was already there is kept."""
        e = self.entry
        for c in e["changes"]:
            ac = c["before_ac"] if render else min(c["before_ac"], c["after"])
            dc = c["before_dc"] if render else min(c["before_dc"], c["after"])
            self.cfg.set_value(e["scheme"], SUB_PROCESSOR, c["alias"], ac=ac, dc=dc)
            c["now"] = ac
        if (self.cache["active"] or {}).get("guid") == e["scheme"]:
            self.cfg.activate(e["scheme"])                    # re-selecting the plan applies the new values
        self.session._save()

    def _revert_locked(self):
        """Put everything back. Raises PolicyError (entry kept, retried later) on failure."""
        if self.entry.get("mode") == "limit":
            self._revert_limits_locked()
        else:
            self._revert_plan_locked()
        if self.entry is None and self.wake is not None:
            self._release_wake()

    def _revert_plan_locked(self):
        entry = self.entry
        try:
            current = self.cfg.active()
            known = {s["guid"] for s in self.cfg.schemes()}
        except OSError as exc:
            raise PolicyError(f"Could not read the power plan: {exc}") from exc
        if current["guid"] not in (entry["after"], entry["now"]):
            self.entry = None
            self.note = f"The power plan was changed by something else ({current['name']}); left as is"
            self.session.log(self.note)
            return
        if entry["before"] not in known:
            self.entry = None
            self.note = f"The original power plan ({entry['before_name']}) no longer exists; staying on {current['name']}"
            self.session.log(self.note, "error")
            return
        try:
            self.cfg.activate(entry["before"])
        except OSError as exc:
            raise PolicyError(f"Could not restore the power plan: {exc}") from exc
        self.cache["active"] = {"guid": entry["before"], "name": entry["before_name"]}
        self.entry, self.note = None, None
        self.session.log(f"Quiet mode ended: power plan {entry['before_name']} restored", "success")

    def _revert_limits_locked(self):
        """Restore each setting that still has the value we set; leave any that something else changed."""
        entry = self.entry
        try:
            known = {s["guid"] for s in self.cfg.schemes()}
        except OSError as exc:
            raise PolicyError(f"Could not read the power plans: {exc}") from exc
        if entry["scheme"] not in known:
            self.entry = None
            self.note = f"The plan {entry['scheme_name']} no longer exists; nothing to restore"
            self.session.log(self.note, "error")
            return
        left_alone, restored, failure = [], [], None
        for c in list(entry["changes"]):
            try:
                current = self.cfg.value(entry["scheme"], SUB_PROCESSOR, c["alias"])
                if current["ac"] != c["now"]:
                    left_alone.append(c["label"])
                else:
                    self.cfg.set_value(entry["scheme"], SUB_PROCESSOR, c["alias"], ac=c["before_ac"], dc=c["before_dc"])
                    restored.append(c["label"])
                entry["changes"].remove(c)                    # done with this one, whichever way
            except OSError as exc:
                failure = exc
        if restored:
            try:
                if (self.cfg.active() or {}).get("guid") == entry["scheme"]:
                    self.cfg.activate(entry["scheme"])
            except OSError as exc:
                failure = failure or exc
        if failure is not None:
            self.session._save()                              # the entry now lists only what is still to do
            raise PolicyError(f"Could not restore the processor settings: {failure}")
        self.entry = None
        self.note = (f"{', '.join(left_alone)} of {entry['scheme_name']} was changed by something else; left as is"
                     if left_alone else None)
        if self.note:
            self.session.log(self.note)
        if restored:
            self.session.log(f"Quiet mode ended: {', '.join(restored)} of {entry['scheme_name']} restored", "success")

    # -- renders ask for the original (or chosen) setting ---------------------------------------

    def render_begin(self):
        with self.session.mutex:
            if self.entry is None:
                self.holds += 1
                return
            with FileLock(self.session.lock_path):
                self._render_begin_locked()

    def render_end(self):
        with self.session.mutex:
            if self.entry is None:
                self.holds = max(0, self.holds - 1)
                return
            with FileLock(self.session.lock_path):
                self._render_end_locked()

    def _render_begin_locked(self):
        self.holds += 1
        if self.holds == 1 and self.entry:
            self._phase_locked(render=True)

    def _render_end_locked(self):
        self.holds = max(0, self.holds - 1)
        if self.holds == 0 and self.entry:
            self._phase_locked(render=False)

    def _phase_locked(self, render):
        why = "render" if render else "quiet"
        try:
            if self.entry.get("mode") == "limit":
                self._set_limits_locked(render)
                label = ", ".join(f"{c['label']} {self._describe(c, c['now'])}" for c in self.entry["changes"])
            else:
                guid = self._render_target() if render else self.entry["after"]
                if self.entry["now"] == guid:
                    return
                self._activate_locked(guid)
                label = (self._find(guid) or {}).get("name", guid)
        except OSError as exc:
            self.session.log(f"Could not change the setting for the {why} phase: {exc}", "error")
            return
        self.session.log(f"Quiet mode, {why} phase: {label}", "info")

    # -- monitor tick -----------------------------------------------------------------------------

    def tick(self, now=None):
        """Keeps the plan list fresh, keeps the laptop awake while work runs, and notices outside changes."""
        if self.cfg is None:
            return
        now = time.time() if now is None else now
        self.refresh(now=now)
        self._update_wake(now)
        if self.entry is None or now - self.checked_at < self.CHECK_EVERY:
            return
        self.checked_at = now
        try:
            active = self.cfg.active()
            current = {}
            if self.entry.get("mode") == "limit":
                scheme = self.entry["scheme"]
                current = {c["alias"]: self.cfg.value(scheme, SUB_PROCESSOR, c["alias"])["ac"] for c in self.entry["changes"]}
        except OSError:
            return
        with self.session.mutex, FileLock(self.session.lock_path):
            entry = self.entry
            if entry is None:
                return
            if entry.get("mode") == "limit":
                moved = active["guid"] != entry["scheme"]
                changed = [c["label"] for c in entry["changes"] if current.get(c["alias"]) != c["now"]]
                if not moved and not changed:
                    return
                try:                                           # takes our values off, leaves anything someone else set
                    self._revert_limits_locked()
                except PolicyError:
                    return                                     # retried at the next check
                self.note = (f"Quiet mode released: the power plan was changed to {active['name']}; the limits on "
                             f"{entry['scheme_name']} were taken off" if moved else
                             f"Quiet mode released: {', '.join(changed)} was changed by something else")
            elif active["guid"] != entry["now"]:
                self.entry = None
                self.note = f"Quiet mode released: the power plan was changed to {active['name']} by something else"
                self.session.log(self.note)
            else:
                return
            self.session._save()
            self.cache["active"] = active
            self._release_wake()

    # -- keep the laptop awake while work runs --------------------------------------------------

    def _release_wake(self):
        if self.wake is not None and self.wake.held:
            self.wake.release()
            self.session.log("The laptop may sleep again", "info")

    def _update_wake(self, now):
        if self.wake is None:
            return
        want = self.entry is not None and self.keep_awake
        if want and self.session.work_in_progress():
            self.busy_at = now
            if not self.wake.held:
                try:
                    self.wake.hold()
                except OSError as exc:
                    self.wake_error = f"Could not keep the laptop awake: {exc}"
                    return
                self.wake_error = None
                self.session.log("Work is running in quiet mode: the laptop is kept awake", "info")
        elif self.wake.held and (not want or self.busy_at is None or now - self.busy_at >= self.WAKE_GRACE):
            self._release_wake()
