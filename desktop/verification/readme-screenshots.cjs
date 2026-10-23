// Regenerate public README screenshots using the shipped RC16 frontend.
// Never open the user's live PLA profile or capture real Tunnel credentials.
// Usage: node desktop/verification/readme-screenshots.cjs
'use strict';
const {chromium}=require('../browser-component/node_modules/playwright');
const {pathToFileURL}=require('node:url');
const fs=require('node:fs');
const path=require('node:path');

const root=path.resolve(__dirname,'../..');
const outDir=path.join(root,'docs/images/pla-desktop');
const release='https://github.com/ymao31196-blip/plus-local-agent/releases/tag/desktop-v1.0.0-rc.16';
const demoSources=[
 {id:'example-skills',kind:'local',location:'D:\\ExampleSkills',cached_skill_count:2,enabled:true,cached_version:'example-cache'},
];
const demoSkills=[
 {source_id:'example-skills',name:'writing-guide',ref:'example-skills:writing-guide',enabled:true,source_enabled:true,effective_enabled:true},
 {source_id:'example-skills',name:'code-review',ref:'example-skills:code-review',enabled:false,source_enabled:true,effective_enabled:false}
];
(async()=>{
 fs.mkdirSync(outDir,{recursive:true});
 const browser=await chromium.launch({channel:'msedge',headless:true});
 try{
   // Live public release page. It is captured anonymously in an isolated browser.
   const github=await browser.newPage({viewport:{width:1360,height:1000},deviceScaleFactor:1});
   await github.goto(release,{waitUntil:'domcontentloaded',timeout:30000});
   const assets=github.locator('details').filter({has:github.getByText('Assets',{exact:true})}).first();
   await assets.waitFor({state:'visible',timeout:18000});
   if(!await assets.getAttribute('open'))await assets.locator('summary').click();
   // GitHub dynamically fetches release assets; fetch that same public fragment
   // explicitly so screenshots never capture its transient “Loading” placeholder.
   const fragmentResponse=await github.request.get(
     'https://github.com/ymao31196-blip/plus-local-agent/releases/expanded_assets/desktop-v1.0.0-rc.16');
   if(!fragmentResponse.ok())throw Error('GitHub public Assets fragment is unavailable');
   const fragment=await fragmentResponse.text();
   await assets.evaluate((el,markup)=>{
     const summary=el.querySelector('summary').outerHTML;
     el.innerHTML=summary+markup;
     el.open=true;
   },fragment);
   const text=await assets.innerText();
   if(!text.includes('PLA.Desktop_1.0.0-rc.16')||!text.includes('SHA256SUMS.txt'))
     throw Error('Live GitHub Assets do not contain the RC16 EXE and checksum; refusing to capture');
   await assets.screenshot({path:path.join(outDir,'01-release-assets.png'),animations:'disabled'});
   await github.close();

   // All Desktop images below are REAL RC16 HTML/CSS rendered in Edge, but
   // the returned management data are examples, never actual credentials.
   const page=await browser.newPage({viewport:{width:1280,height:880},deviceScaleFactor:1});
   const errors=[];
   page.on('pageerror',error=>errors.push(error.message));
   await page.addInitScript(({sources,skills})=>{
     window.readmeDemoConnected=false;
     window.__TAURI__={core:{invoke:async(method,request)=>{
       if(method!=='manage')throw Error('Unexpected native API');
       const {command}=request;
       switch(command){
         case 'status':return {
           config:{version:'1.0.0-rc.16',onboarding_complete:true,
             data_directory:'C:\\Users\\Example\\AppData\\Local\\PLA Desktop',
             runtime_port:18081,health_port:18082,browser_port:8931,
             browser_enabled:false,autostart:false,tunnel_id:'',credential_saved:false},
           runtime:window.readmeDemoConnected?'ready':'stopped',
           tunnel:window.readmeDemoConnected?'ready':'stopped',browser:'disabled',
           workspaces:{roots:{demo:{path:'D:\\PLA-Test',read:true,write:false,execute:false}},
             config_sha256:'example-configuration'}
         };
         case 'skill_action':return {data:
           request.args.action==='sources'?{sources}:request.args.action==='states'?{skills}:{}};
         case 'skill_permissions':return {available_roots:{},local_roots:[],writable_roots:[]};
         case 'provider_catalog':return {manifest_directory:'local-example',runtime_observed:true,providers:[
           {provider_id:'skill-library',execution_files_present:true,requested_enabled:true,
            lifecycle:{state:'ready',enabled:true,tool_count:17},install_supported:true}
         ]};
         case 'installation_status':return {jobs:[],installed_receipts:['skill-library']};
         case 'provider_installed_list':return {providers:[]};
         case 'repository_list':return {repositories:[]};
         case 'provider_discover':return {providers:[],total:0,installed_count:0};
         case 'activity_recent':return {available:false,activities:[]};
         default:throw Error('Mock has no operation '+command);
       }
     }}};
   },{sources:demoSources,skills:demoSkills});
   await page.goto(pathToFileURL(path.join(root,'desktop/ui/index.html')).href);
   await page.waitForFunction(()=>!document.querySelector('#refresh').disabled,{timeout:15000});

   await page.locator('nav [data-view="setup"]').click();
   await page.locator('#secret').evaluate(el=>{el.value='';});
   await page.screenshot({path:path.join(outDir,'02-first-setup.png'),animations:'disabled'});

   await page.locator('nav [data-view="workspaces"]').click();
   await page.screenshot({path:path.join(outDir,'03-workspace-permissions.png'),animations:'disabled'});

   await page.evaluate(async()=>{window.readmeDemoConnected=true;await refresh();});
   await page.locator('nav [data-view="overview"]').click();
   await page.screenshot({path:path.join(outDir,'04-runtime-and-tunnel.png'),animations:'disabled'});

   await page.locator('nav [data-view="component-management"]').click();
   await page.waitForFunction(()=>document.querySelector('#provider-list')?.textContent.includes('skill-library'),{timeout:15000});
   await page.screenshot({path:path.join(outDir,'05-components.png'),animations:'disabled'});

   await page.locator('nav [data-view="skill-list"]').click();
   await page.waitForFunction(()=>document.querySelector('#skill-list-status').textContent.includes('已缓存2个Skill'),{timeout:15000});
   await page.screenshot({path:path.join(outDir,'06-skill-library.png'),animations:'disabled'});

   await page.close();
   if(errors.length)throw Error('Frontend errors: '+errors.join(' | '));
   console.log('Saved six RC16 README screenshots to '+outDir);
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
