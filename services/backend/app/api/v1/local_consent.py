"""Explicit human consent for native MCP OAuth; session credentials stay in memory."""
from html import escape
import json
from urllib.parse import urlencode

from fastapi import Request
from fastapi.responses import HTMLResponse


def consent_page(request: Request, client_name: str | None = None, *,
                 resolved_scope: str | None = None, resolved_resource: str | None = None) -> HTMLResponse:
    params = dict(request.query_params)
    if resolved_scope is not None:
        params["scope"] = resolved_scope
    if resolved_resource is not None:
        params["resource"] = resolved_resource
    encoded = json.dumps(params).replace("<", "\\u003c").replace(">", "\\u003e")
    name = escape(client_name or params.get("client_id", "MCP client"))
    scope = escape(params.get("scope") or "Read coordination objects")
    resource = escape(params.get("resource") or "Commonflame MCP")
    next_path = request.url.path + ("?" + request.url.query if request.url.query else "")
    login = escape('/login?' + urlencode({'next': next_path}), quote=True)
    signup = escape('/signup?' + urlencode({'next': next_path}), quote=True)
    html = """<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Approve a connection · Commonflame</title>
<style>body{font:16px system-ui;background:#171d24;color:#edf2f5;margin:0;padding:5vh 20px}
main{max-width:560px;margin:auto;padding:32px;border:1px solid #394451;border-radius:20px}
h1{font-size:28px}p{line-height:1.6;color:#b9c5d2}button{font:inherit;padding:12px;border-radius:8px;border:0;font-weight:600;cursor:pointer;background:#d09c68}
button:disabled{opacity:.45;cursor:default}code{overflow-wrap:anywhere}a{color:#dcb48b}#status{min-height:24px}.actions{display:flex;gap:12px;flex-wrap:wrap}</style>
<main><small>COMMONFLAME</small><h1>Approve this agent connection</h1>
<p><strong>__CLIENT__</strong> is asking for access to <code>__RESOURCE__</code>.</p>
<p>Requested permissions: <code>__SCOPE__</code></p>
<p>This client receives its own agent identity, sponsored by you in your current workspace.</p>
<p id="identity">Checking your session…</p>
<p id="signin" hidden><a href="__LOGIN__">Sign in</a> · <a href="__SIGNUP__">Set up an invited account</a></p>
<div class="actions"><button id="approve" disabled>Approve connection</button><button id="deny" disabled>Deny</button></div>
<p id="status" role="status"></p><p><a href="/ax">Return to Commonflame</a></p></main>
<script>
const params = __PARAMS__;
const status = document.getElementById('status');
const approve = document.getElementById('approve');
const deny = document.getElementById('deny');
let token = '';
async function session(){
  try{
    const refreshSession=()=>fetch('/auth/local/refresh',{method:'POST',credentials:'same-origin'});
    const r=await(navigator.locks?.request ? navigator.locks.request('waystation-refresh-cookie',refreshSession) : refreshSession());
    if(!r.ok)throw new Error('Sign in before deciding');
    const d=await r.json();token=d.access_token;
    document.getElementById('identity').textContent='Sponsor: '+d.user.username+' · Workspace: '+d.space_id;
    approve.disabled=false;deny.disabled=false;
  }catch(e){document.getElementById('identity').textContent='Sign in to review this connection.';document.getElementById('signin').hidden=false;}
}
session();
async function decide(approved){
  approve.disabled=true;deny.disabled=true;status.textContent=approved?'Approving…':'Declining…';
  try{
    const r=await fetch('/oauth/authorize',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json','Authorization':'Bearer '+token},body:JSON.stringify({...params,approved})});
    const d=await r.json();if(!r.ok)throw new Error(typeof d.detail==='string'?d.detail:'Could not complete this decision');
    location.assign(d.redirect_uri);
  }catch(e){status.textContent=e.message;approve.disabled=false;deny.disabled=false;}
}
approve.addEventListener('click',()=>decide(true));deny.addEventListener('click',()=>decide(false));
</script></html>"""
    for key,value in {'__CLIENT__':name,'__RESOURCE__':resource,'__SCOPE__':scope,'__PARAMS__':encoded,'__LOGIN__':login,'__SIGNUP__':signup}.items():
        html=html.replace(key,value)
    return HTMLResponse(html, headers={"Cache-Control":"no-store","X-Frame-Options":"DENY"})
