(function(){"use strict";
var $=function(s,r){return(r||document).querySelector(s)},$$=function(s,r){return[].slice.call((r||document).querySelectorAll(s))};
var P=window.POSTS||[],D=window.DH||{};
var ST={get:function(k,d){try{var v=JSON.parse(localStorage.getItem(k));return v==null?d:v}catch(e){return d}},set:function(k,v){try{localStorage.setItem(k,JSON.stringify(v))}catch(e){}}};
var BM='<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 3h12a1 1 0 0 1 1 1v17l-7-4.5L5 21V4a1 1 0 0 1 1-1z"/></svg>';
var TE={"Python":"🐍","Flutter":"🦋","C++":"⚙️","JavaScript":"🌐","Java":"☕"};
function esc(s){return String(s).replace(/[&<>"]/g,function(c){return{"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]})}
function toast(m){var t=$("#toast");if(!t)return;t.textContent=m;t.classList.add("show");clearTimeout(toast.t);toast.t=setTimeout(function(){t.classList.remove("show")},1800)}
function card(p){return'<article class="card post" data-tech="'+esc(p.tech.join(","))+'"><a class="cover c'+p.c+'" href="'+p.url+'" aria-label="'+esc(p.title)+'"><span>'+(TE[p.tech[0]]||"💻")+'</span></a><div class="pbody"><div class="row">'+p.tech.map(function(t){return'<span class="chip">'+(TE[t]||"")+" "+esc(t)+"</span>"}).join("")+(p.series?'<span class="chip alt">Part '+p.part+"</span>":"")+'</div><h3><a href="'+p.url+'">'+esc(p.title)+'</a></h3><p class="muted">'+esc(p.desc)+'</p><div class="row between tiny muted"><span>'+p.date+" · "+p.mins+' min read</span><button type="button" class="bm" data-slug="'+p.slug+'" aria-pressed="false" aria-label="Bookmark this article">'+BM+"</button></div></div></article>"}
// theme
var tb=$("#themeBtn");if(tb)tb.onclick=function(){var t=document.documentElement.dataset.theme==="dark"?"light":"dark";document.documentElement.dataset.theme=t;try{localStorage.setItem("dh-theme",t)}catch(e){}};
// drawer
var dr=$("#drawer"),sc=$("#scrim"),mb=$("#menuBtn");function tog(o){dr.classList.toggle("open",o);sc.classList.toggle("show",o)}
if(mb)mb.onclick=function(){tog(true)};if(sc)sc.onclick=function(){tog(false)};
document.addEventListener("keydown",function(e){if(e.key==="Escape")tog(false);if(e.key==="/"&&!/INPUT|TEXTAREA|SELECT/.test(document.activeElement.tagName)){e.preventDefault();$("#q").focus()}});
var path=location.pathname;$$("#nav a").forEach(function(a){var h=a.getAttribute("data-path");if(h==="/"?path==="/":path.indexOf(h)===0)a.classList.add("active")});
// bookmarks (localStorage)
function bms(){var b=ST.get("dh-bm",[]);return Array.isArray(b)?b:[]}
function paint(){var b=bms();$$(".bm").forEach(function(x){var on=b.indexOf(x.dataset.slug)>-1;x.setAttribute("aria-pressed",on?"true":"false");var l=x.querySelector(".bml");if(l)l.textContent=on?"Saved":"Save"});
$$("[data-bmc]").forEach(function(c){c.textContent=b.length;c.hidden=!b.length})}
function renderBm(){var l=$("#bmlist"),b=bms(),r=P.filter(function(p){return b.indexOf(p.slug)>-1});l.innerHTML=r.map(card).join("");$("#bmempty").hidden=r.length>0;paint()}
document.addEventListener("click",function(e){var x=e.target.closest(".bm");if(!x)return;e.preventDefault();var b=bms(),i=b.indexOf(x.dataset.slug);if(i>-1)b.splice(i,1);else b.push(x.dataset.slug);ST.set("dh-bm",b);toast(i>-1?"Removed from bookmarks":"Saved to bookmarks");if($("#bmlist"))renderBm();else paint()});
// live search (dropdown updates on every keystroke)
var q=$("#q"),box=$("#results");
function hay(p){return(p.title+" "+p.desc+" "+p.tech.join(" ")).toLowerCase()}
function find(t){var w=t.toLowerCase().split(/\s+/).filter(Boolean);return P.filter(function(p){var h=hay(p);return w.every(function(x){return h.indexOf(x)>-1})})}
function search(){var v=q.value.trim();if(!v){box.hidden=true;return}var r=find(v).slice(0,8);
box.innerHTML=r.length?r.map(function(p){return'<a href="'+p.url+'">'+(TE[p.tech[0]]||"")+" "+esc(p.title)+"<small>"+esc(p.desc)+"</small></a>"}).join(""):'<a href="/#latest">No match for “'+esc(v)+'”<small>Try Python, Flutter, C++ or a keyword</small></a>';box.hidden=false}
if(q){["input","keyup","search","focus"].forEach(function(ev){q.addEventListener(ev,search)});
q.addEventListener("keydown",function(e){if(e.key==="Enter"&&q.value.trim()){var r=find(q.value.trim());if(r[0])location.href=r[0].url}});
document.addEventListener("click",function(e){if(!e.target.closest(".search-wrap"))box.hidden=true})}
// home list: filter, sort, layout, infinite scroll (20 per load)
var host=$("#posts");
if(host&&$("#filters")){
var S={f:"all",s:"new",n:20,v:ST.get("dh-view","1")==="g"?"g":"1"};
function list(){var r=P.filter(function(p){return S.f==="all"||p.tech.indexOf(S.f)>-1});r.sort(S.s==="pop"?function(a,b){return b.views-a.views}:function(a,b){return a.date<b.date?1:-1});return r}
function info(r){$("#more").textContent=r.length>S.n?"Scroll down for more…":r.length+(r.length===1?" article":" articles")}
function draw(){var r=list();S.n=20;host.innerHTML=r.length?r.slice(0,20).map(card).join(""):'<div class="card center empty"><h3>No articles yet</h3><p class="muted">Try another tech filter.</p></div>';info(r);paint()}
function view(){host.className="posts v"+S.v;$$(".seg-b").forEach(function(b){b.classList.toggle("on",b.dataset.v===S.v)})}
$("#filters").addEventListener("click",function(e){var b=e.target.closest("[data-f]");if(!b)return;S.f=b.dataset.f;$$("[data-f]",$("#filters")).forEach(function(x){x.classList.toggle("on",x===b)});draw()});
$("#sort").addEventListener("change",function(e){S.s=e.target.value;draw()});
$$(".seg-b").forEach(function(b){b.addEventListener("click",function(){S.v=b.dataset.v;ST.set("dh-view",S.v);view()})});
function more(){var r=list();if(r.length<=S.n)return;var a=S.n;S.n+=20;host.insertAdjacentHTML("beforeend",r.slice(a,S.n).map(card).join(""));info(r);paint()}
if("IntersectionObserver"in window)new IntersectionObserver(function(en){if(en[0].isIntersecting)more()},{rootMargin:"400px"}).observe($("#sentinel"));
view();draw()}
// bookmarks page
if($("#bmlist"))renderBm();
paint();
// article extras
var art=$(".article"),pr=$("#progress"),up=$("#toTop"),tick=false;
function onScroll(){tick=false;var y=scrollY;if(up)up.hidden=y<600;if(art&&pr){var r=art.getBoundingClientRect(),h=art.offsetHeight-innerHeight;pr.style.width=Math.max(0,Math.min(100,-r.top/Math.max(h,1)*100))+"%"}}
addEventListener("scroll",function(){if(!tick){tick=true;requestAnimationFrame(onScroll)}},{passive:true});onScroll();
if(up)up.onclick=function(){scrollTo({top:0,behavior:"smooth"})};
document.addEventListener("click",function(e){var s=e.target.closest("[data-share]");if(!s)return;var d={title:document.title,url:location.href};
if(navigator.share){navigator.share(d).catch(function(){})}else if(navigator.clipboard){navigator.clipboard.writeText(d.url).then(function(){toast("Link copied")})}});
$$(".prose pre").forEach(function(pre){var b=document.createElement("button");b.type="button";b.className="cp";b.textContent="Copy";b.onclick=function(){var t=pre.querySelector("code").innerText;(navigator.clipboard?navigator.clipboard.writeText(t):Promise.reject()).then(function(){b.textContent="Copied";setTimeout(function(){b.textContent="Copy"},1500)}).catch(function(){toast("Copy not supported")})};pre.appendChild(b)});
// optional analytics/ads from config.js
function sj(src,a){var e=document.createElement("script");e.async=true;e.src=src;for(var k in a)e.setAttribute(k,a[k]);document.head.appendChild(e)}
if(D.ga_id){sj("https://www.googletagmanager.com/gtag/js?id="+D.ga_id);window.dataLayer=window.dataLayer||[];window.gtag=function(){dataLayer.push(arguments)};gtag("js",new Date());gtag("config",D.ga_id)}
if(D.cf_beacon)sj("https://static.cloudflareinsights.com/beacon.min.js",{defer:"","data-cf-beacon":JSON.stringify({token:D.cf_beacon})});
if(D.adsense_id)sj("https://pagead2.googlesyndication.com/pagead/js/adsbygoogle.js?client="+D.adsense_id,{crossorigin:"anonymous"});
})();
