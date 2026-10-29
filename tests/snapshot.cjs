// A snapshot shaped like loadmanager.engine.Monitor.get() on Windows.
const nativeSnapshot=()=>({
  demo:false,platform:'Windows',machine:'AMD64',cores:24,cpu_name:'x',cpu:20,memory_percent:75,memory_total:32,
  memory_used:24,memory_available:8,disk_rate:3,disk_free:100,disk_percent:50,battery:null,trim_threshold:70,
  history:[{cpu:20,memory:75}],recommendation:'focus',timestamp:1,active:false,profile:null,changed:0,events:[],
  session_started:null,guardian_ready:true,stats:{},
  benchmark:{running:false,progress:0,results:[],baseline:null,with_session:null,delta_percent:null,error:null},
  processes:[
    {pid:10,created:1,name:'chrome.exe',exe:'c',owner:'u',cpu:2,memory:500,status:'running',eligible:true,protected:false,managed:false,app:'chrome',kind:'heavy'},
    {pid:11,created:2,name:'chrome.exe',exe:'c',owner:'u',cpu:1,memory:400,status:'running',eligible:true,protected:false,managed:false,app:'chrome',kind:'heavy'},
    {pid:12,created:3,name:'Spotify.exe',exe:'s',owner:'u',cpu:0,memory:200,status:'running',eligible:true,protected:false,managed:false,app:'spotify',kind:'light'}],
  groups:[{key:'chrome',title:'Google Chrome',actions:['priority','eco','trim'],available:true,count:2,eligible:2,managed:0,cpu:3,memory:900}],
  auto:{enabled:false,state:'off',groups:['chrome'],available_groups:[{key:'chrome',title:'Google Chrome'},{key:'claude',title:'Claude'}],grace:20,start_after:3,error:null,owned:false,paused:false,jobs:[]},
  timed:{running:false,mode:null,phase:null,phase_started:null,now:1000,error:null,command:'saved command',cwd:'',order:'baseline_first',results:[],comparisons:[]},
  quiet:{available:true,active:false,state:'off',mode:'plan',cap:100,boost_off:true,keep_awake:true,awake:false,limits:null,scheme:null,render_scheme:null,quiet_target:{guid:'64a64f24-65b9-4b56-befd-5ec1eaced9b3',name:'Silent'},original:null,
    current:{guid:'6fecc5ae-f350-48a5-b669-b472cb895ccf',name:'Turbo'},auto_enter:false,since:null,note:null,error:null,
    schemes:[{guid:'27fa6203-3987-4dcc-918d-748559d549ec',name:'Performance',active:false},{guid:'381b4222-f694-41f0-9685-ff5bb260df2e',name:'Balanced',active:false},
             {guid:'64a64f24-65b9-4b56-befd-5ec1eaced9b3',name:'Silent',active:false},{guid:'6fecc5ae-f350-48a5-b669-b472cb895ccf',name:'Turbo',active:true}]},
  recorder:{enabled:true,on:40,off:25,current:null,spikes:[],top:[],count:0},
  video:{running:false,title:null,status:null,progress:null,close_asked:0,queued:0,paused:false,after:'nothing'}
});
module.exports=nativeSnapshot;
