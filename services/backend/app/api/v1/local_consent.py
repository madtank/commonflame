"""A deliberate browser approval screen for public MCP OAuth clients."""
from html import escape
import json

from fastapi import Request
from fastapi.responses import HTMLResponse


def consent_page(request: Request, client_name: str | None = None) -> HTMLResponse:
    params = dict(request.query_params)
    encoded = json.dumps(params).replace("<", "\\u003c").replace(">", "\\u003e")
    name = escape(client_name or params.get("client_id", "MCP client"))
    scope = escape(params.get("scope", "openid"))
    resource = escape(params.get("resource", "Default MCP resource"))
    html = """<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Approve a connection · Waystation</title>
<style>body{font:16px system-ui;background:#171d24;color:#edf2f5;margin:0;padding:5vh 20px}
main{max-width:520px;margin:auto;padding:32px;border:1px solid #394451;border-radius:20px}
h1{font-size:28px}p{line-height:1.6;color:#b9c5d2}label{display:block;margin:16px 0}
input,button{font:inherit;padding:12px;border-radius:8px;box-sizing:border-box;width:100%}
input{background:#222c38;color:#fff;border:1px solid #536274}button{background:#d09c68;border:0;font-weight:600;cursor:pointer}
code{overflow-wrap:anywhere}a{color:#dcb48b}#status{min-height:24px}</style>
<main><small>WAYSTATION</small><h1>Approve this connection</h1>
<p><strong>__CLIENT__</strong> is asking for access to <code>__RESOURCE__</code>.</p>
<p>Requested permissions: <code>__SCOPE__</code></p>
<form><div id="credentials"><label>Username<input name="username" autocomplete="username" required></label>
<label>Password<input name="password" type="password" autocomplete="current-password" required></label></div>
<p id="identity"></p><button type="submit">Sign in and approve</button></form>
<p id="status" role="status"></p><p><a href="/ax">Cancel and return to Waystation</a></p></main>
<script>
const params = __PARAMS__;
const form = document.querySelector('form');
const status = document.getElementById('status');
let token = '';
async function session(){
  try {
    const r=await fetch('/auth/local/refresh',{method:'POST',credentials:'same-origin'});
    if(r.ok){const d=await r.json();token=d.access_token;document.getElementById('identity').textContent='Signed in as '+d.user.username;
      document.getElementById('credentials').hidden=true;form.username.required=false;form.password.required=false;
      form.querySelector('button').textContent='Approve connection';}
  }catch(e){}
}
session();
form.addEventListener('submit',async e=>{
  e.preventDefault();const button=form.querySelector('button');button.disabled=true;status.textContent='Approving…';
  try{
    if(!token){const r=await fetch('/auth/local/login',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({username:form.username.value,password:form.password.value})});
      const d=await r.json();form.password.value='';if(!r.ok)throw new Error(d.detail||'Sign in failed');token=d.access_token;}
    const r=await fetch('/oauth/authorize',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json','Authorization':'Bearer '+token},body:JSON.stringify(params)});
    const d=await r.json();if(!r.ok)throw new Error(typeof d.detail==='string'?d.detail:'Approval failed');location.assign(d.redirect_uri);
  }catch(e){status.textContent=e.message;button.disabled=false;}
});
</script></html>"""
    html = html.replace("__CLIENT__", name).replace("__RESOURCE__", resource)
    html = html.replace("__SCOPE__", scope).replace("__PARAMS__", encoded)
    return HTMLResponse(html, headers={"Cache-Control": "no-store", "X-Frame-Options": "DENY"})
