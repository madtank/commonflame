"""Native public OAuth discovery, explicit consent, and code-binding contracts."""
import hashlib
import json
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from starlette.requests import Request

from app.api.v1 import oauth_as
from app.core import ax_jwt
from app.core.jwt_verify import _enforce_oauth_request_scope
from app.models.oauth_as import OAuthAuthorizationCode, OAuthClient, OAuthRefreshToken


class Scalar:
    def __init__(self, value): self.value = value
    def scalar_one_or_none(self): return self.value


def client_record():
    return SimpleNamespace(client_id='known-client', client_name='Test MCP client', is_active=True,
        redirect_uris=['http://127.0.0.1:6274/callback'], grant_types=['authorization_code','refresh_token',oauth_as.DEVICE_CODE_GRANT],
        response_types=['code'], token_endpoint_auth_method='none', scope=None, client_secret=None)


def request(path='/oauth/authorize', body=None, method='POST', headers=None):
    data=json.dumps(body or {}).encode()
    pairs=[(b'content-type',b'application/json')]+[(k.encode(),v.encode()) for k,v in (headers or {}).items()]
    return Request({'type':'http','method':method,'path':path,'scheme':'http','server':('localhost',3000),'headers':pairs,'query_string':b''},
                   receive=AsyncMock(return_value={'type':'http.request','body':data,'more_body':False}))


@pytest.fixture(autouse=True)
def public_env(monkeypatch):
    monkeypatch.setenv('AUTH_MODE','builtin')
    monkeypatch.setenv('AX_AUTH_PUBLIC_BASE_URL','http://localhost:3000')
    monkeypatch.setenv('AX_MCP_RESOURCE_URL','http://localhost:3000/mcp')
    monkeypatch.setenv('FRONTEND_URL','http://localhost:3000')


def authorize_params():
    return dict(response_type='code',client_id='known-client',redirect_uri='http://127.0.0.1:6274/callback',
        scope='tasks.read',state='state-sentinel',resource='http://localhost:3000/mcp',
        code_challenge=oauth_as._pkce_challenge('A'*64),code_challenge_method='S256')


def fake_db():
    db=AsyncMock(); db.add=MagicMock(); db.get=AsyncMock(return_value=client_record())
    db.scalar=AsyncMock(return_value=True); db.execute=AsyncMock(return_value=Scalar(None))
    db.begin_nested=MagicMock(return_value=AsyncMock())
    return db


def test_discovery_uses_configured_origin_never_forwarded_host():
    app=FastAPI();app.include_router(oauth_as.router)
    with TestClient(app) as client:
        response=client.get('/.well-known/oauth-authorization-server',headers={'Host':'evil.example','X-Forwarded-Host':'evil.example','X-Forwarded-Proto':'https'})
    data=response.json()
    assert data['issuer']=='http://localhost:3000'
    assert data['authorization_endpoint']=='http://localhost:3000/oauth/authorize'
    assert data['registration_endpoint']=='http://localhost:3000/oauth/register'
    assert data['mcp']['resource']=='http://localhost:3000/mcp'
    assert data['code_challenge_methods_supported']==['S256']


@pytest.mark.parametrize('resource',['https://evil.example/mcp','http://localhost:3000/mcp?x=1','http://localhost:3000/mcp#fragment','http://localhost:3000/mcp/agents/','http://localhost:3000/mcp/agents/name/extra'])
def test_resource_rejects_foreign_servers_and_non_resource_components(resource):
    with pytest.raises(HTTPException) as exc: oauth_as._validated_resource_url(resource)
    assert exc.value.status_code==400


def test_canonical_resource_and_named_alias_are_accepted():
    assert oauth_as._validated_resource_url(None)=='http://localhost:3000/mcp'
    assert oauth_as._validate_device_resource_url('http://localhost:3000/mcp')=='http://localhost:3000/mcp'
    assert oauth_as._validated_resource_url('http://localhost:3000/mcp/agents/researcher').endswith('/researcher')


@pytest.mark.asyncio
async def test_get_authorize_requires_explicit_consent_even_with_bearer(monkeypatch):
    db=fake_db(); params=authorize_params()
    req=request(method='GET',headers={'authorization':'Bearer ignored'})
    response=await oauth_as.authorize_client(req,db=db,**params)
    assert response.status_code==200
    assert b'Approve this agent connection' in response.body
    assert b'/auth/local/refresh' in response.body
    db.add.assert_not_called()


@pytest.mark.asyncio
async def test_consent_denial_redirects_only_to_registered_callback(monkeypatch):
    db=fake_db(); user=SimpleNamespace(id=uuid.uuid4(),active=True,auth_provider='builtin')
    session=SimpleNamespace(user=user,space_id=str(uuid.uuid4()),db=db,is_agent=False,principal_type='user')
    monkeypatch.setattr(oauth_as,'get_secure_session',AsyncMock(return_value=session))
    response=await oauth_as.approve_authorization(request(body={**authorize_params(),'approved':False},headers={'authorization':'Bearer test-session'}),db)
    redirect=json.loads(response.body)['redirect_uri']; query=parse_qs(urlparse(redirect).query)
    assert query['error']==['access_denied'] and query['state']==['state-sentinel']
    assert 'code' not in query
    db.add.assert_not_called()


@pytest.mark.asyncio
async def test_authorize_missing_explicit_decision_or_bearer_does_not_issue():
    db=fake_db()
    with pytest.raises(HTTPException) as exc:
        await oauth_as.approve_authorization(request(body=authorize_params(),headers={'authorization':'Bearer test-session'}),db)
    assert exc.value.status_code==400
    with pytest.raises(HTTPException) as exc:
        await oauth_as.approve_authorization(request(body={**authorize_params(),'approved':True}),db)
    assert exc.value.status_code==401
    db.add.assert_not_called()


@pytest.mark.asyncio
async def test_agent_or_non_member_cannot_sponsor_another_agent():
    db=fake_db();user=SimpleNamespace(id=uuid.uuid4(),active=True,auth_provider='builtin')
    session=SimpleNamespace(user=user,space_id=str(uuid.uuid4()),db=db,is_agent=True,principal_type='agent')
    with pytest.raises(HTTPException) as exc: await oauth_as._require_human_sponsor(session)
    assert exc.value.status_code==403
    session.is_agent=False;session.principal_type='user';db.scalar.return_value=False
    with pytest.raises(HTTPException) as exc: await oauth_as._require_human_sponsor(session)
    assert exc.value.status_code==403


@pytest.mark.asyncio
async def test_pkce_approval_persists_exact_sponsor_workspace(monkeypatch):
    db=fake_db();user=SimpleNamespace(id=uuid.uuid4(),active=True,auth_provider='builtin');space=uuid.uuid4()
    session=SimpleNamespace(user=user,space_id=str(space),db=db,is_agent=False,principal_type='user')
    monkeypatch.setattr(oauth_as,'get_secure_session',AsyncMock(return_value=session))
    response=await oauth_as.approve_authorization(request(body={**authorize_params(),'approved':True},headers={'authorization':'Bearer test-session'}),db)
    record=db.add.call_args.args[0]
    assert isinstance(record,OAuthAuthorizationCode) and record.authorized_space_id==space
    code=parse_qs(urlparse(json.loads(response.body)['redirect_uri']).query)['code'][0]
    assert hashlib.sha256(code.encode()).hexdigest()==record.code_hash
    assert record.owner_user_id==user.id and record.resource=='http://localhost:3000/mcp'


@pytest.mark.asyncio
@pytest.mark.parametrize('change',[{'redirect_uri':'http://127.0.0.1:9999/wrong'}, {'code_verifier':'wrong'}, {'resource':'https://evil.example/mcp'}])
async def test_code_exchange_redirect_pkce_resource_binding(change):
    db=fake_db();record=SimpleNamespace(client_id='known-client',redirect_uri='http://127.0.0.1:6274/callback',
        code_challenge=oauth_as._pkce_challenge('A'*64),resource='http://localhost:3000/mcp',
        expires_at=datetime.now(timezone.utc)+timedelta(minutes=5),consumed_at=None)
    db.execute.return_value=Scalar(record)
    params={'code':'synthetic-code','client_id':'known-client','redirect_uri':record.redirect_uri,'code_verifier':'A'*64,**change}
    response=await oauth_as._handle_authorization_code(params,db)
    assert response.status_code==400 and record.consumed_at is None
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_canonical_clients_receive_independent_signed_agent_credentials(monkeypatch):
    db=fake_db(); owner=uuid.uuid4(); space=uuid.uuid4();user=SimpleNamespace(id=owner,active=True,auth_provider='builtin')
    async def get(model,key): return user if model.__name__=='User' else client_record()
    db.get.side_effect=get
    monkeypatch.setattr(oauth_as,'grant_space_access',AsyncMock())
    issued=[]
    for client_id in ['client-one','client-two']:
        response=await oauth_as._issue_oauth_token_response(db,client_id=client_id,owner_user_id=str(owner),
            scope='tasks.read',audience='http://localhost:3000/mcp',src_credential_id='device:synthetic',authorized_space_id=str(space))
        import jwt
        _,public=ax_jwt._get_signing_key()
        claims=jwt.decode(response['access_token'],public,algorithms=['RS256'],issuer='ax-backend',audience='http://localhost:3000/mcp')
        assert claims['token_class']=='agent_access' and claims['owner_user_id']==str(owner)
        assert claims['authorized_space_id']==str(space) and claims['exp']-claims['iat']==900
        issued.append(claims['agent_id'])
    assert issued[0]!=issued[1]
    agents=[call.args[0] for call in db.add.call_args_list if call.args[0].__class__.__name__=='Agent']
    assert agents[0].name!=agents[1].name


@pytest.mark.parametrize('path,method,allowed', [('/api/v1/tasks','GET',True),('/api/v1/tasks','POST',False),('/api/v1/messages','GET',False)])
def test_granular_oauth_scope_enforced_on_business_requests(path,method,allowed):
    claims={'token_class':'agent_access','src_credential_id':'device:synthetic','scope':'tasks.read'}
    if allowed: _enforce_oauth_request_scope(claims,request(path,method=method))
    else:
        with pytest.raises(HTTPException) as exc: _enforce_oauth_request_scope(claims,request(path,method=method))
        assert exc.value.status_code==403


@pytest.mark.asyncio
async def test_refresh_cannot_change_approved_resource_or_expand_scope():
    db=fake_db();record=SimpleNamespace(client_id='known-client',scope='tasks.read',resource='http://localhost:3000/mcp',
        expires_at=datetime.now(timezone.utc)+timedelta(days=1),revoked_at=None)
    db.execute.return_value=Scalar(record)
    response=await oauth_as._handle_refresh_token({'client_id':'known-client','refresh_token':'synthetic','resource':'https://evil.example/mcp'},db)
    assert response.status_code==400 and record.revoked_at is None
    with pytest.raises(HTTPException) as exc:
        await oauth_as._handle_refresh_token({'client_id':'known-client','refresh_token':'synthetic','scope':'tasks.write'},db)
    assert exc.value.status_code==400 and record.revoked_at is None


@pytest.mark.asyncio
async def test_displayed_consent_matches_resolved_registered_grant():
    db=fake_db(); client=client_record();client.scope='ax-api/mcp:read ax-api/mcp:write';db.get.return_value=client
    params=authorize_params();params.pop('scope')
    response=await oauth_as.authorize_client(request(method='GET'),db=db,**params)
    assert b'ax-api/mcp:read ax-api/mcp:write' in response.body
    assert b'"scope": "ax-api/mcp:read ax-api/mcp:write"' in response.body
    assert b'waystation-refresh-cookie' in response.body


@pytest.mark.asyncio
async def test_expired_device_code_never_reports_approval(monkeypatch):
    db=fake_db();record=SimpleNamespace(expires_at=datetime.now(timezone.utc)-timedelta(seconds=1),status='pending')
    lookup=AsyncMock(return_value=record);monkeypatch.setattr(oauth_as,'_find_device_by_user_code',lookup)
    principal=oauth_as.DeviceApprovalPrincipal(user_id=str(uuid.uuid4()),space_id=str(uuid.uuid4()))
    response=await oauth_as.approve_device_code(request('/oauth/device/approve',body={'user_code':'TESTCODE','approved':True}),principal,db)
    assert response.status_code==400 and record.status=='pending'
    assert lookup.call_args.kwargs['for_update'] is True
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_native_pages_share_browser_refresh_lock_and_never_embed_session_tokens():
    response=await oauth_as.device_approval_page('TESTCODE')
    assert b'waystation-refresh-cookie' in response.body
    assert b"'Authorization':'Bearer '+token" in response.body
    assert b'jwt_token' not in response.body and b'localStorage' not in response.body


@pytest.mark.asyncio
async def test_refresh_rotates_preserving_agent_and_approved_workspace(monkeypatch):
    db=fake_db();owner=uuid.uuid4();space=uuid.uuid4();agent_id=uuid.uuid4()
    user=SimpleNamespace(id=owner,active=True,auth_provider='builtin',current_space_id=uuid.uuid4())
    agent=SimpleNamespace(id=agent_id,user_id=owner,space_id=space,name='client-bound-agent',status='active')
    record=SimpleNamespace(id=uuid.uuid4(),client_id='known-client',owner_user_id=owner,agent_id=agent_id,
        authorized_space_id=space,scope='tasks.read',resource='http://localhost:3000/mcp',
        expires_at=datetime.now(timezone.utc)+timedelta(days=1),revoked_at=None)
    async def get(model,key):
        if model.__name__=='User': return user
        if model.__name__=='Agent': return agent
        return client_record()
    db.get.side_effect=get;db.execute.side_effect=[Scalar(record),Scalar(None)]
    response=await oauth_as._handle_refresh_token({'client_id':'known-client','refresh_token':'synthetic'},db)
    import jwt
    _,public=ax_jwt._get_signing_key()
    claims=jwt.decode(response['access_token'],public,algorithms=['RS256'],issuer='ax-backend',audience='http://localhost:3000/mcp')
    assert claims['agent_id']==str(agent_id) and claims['authorized_space_id']==str(space)
    assert record.revoked_at is not None
    rotated=db.add.call_args.args[0]
    assert isinstance(rotated,OAuthRefreshToken) and rotated.rotated_from_id==record.id
    replay=await oauth_as._handle_refresh_token({'client_id':'known-client','refresh_token':'synthetic'},db)
    assert replay.status_code==400 and json.loads(replay.body)['error']=='invalid_grant'


def test_secret_fields_are_not_echoed_in_validation_errors():
    from api.main import validation_error_without_credentials
    from fastapi.exceptions import RequestValidationError
    import asyncio
    secret = "short-private-capability"
    exc = RequestValidationError([{"loc": ("body", "token"), "msg": "Invalid value", "type": "string_too_short", "input": secret}])
    response = asyncio.run(validation_error_without_credentials(request(), exc))
    assert response.status_code == 422
    assert secret.encode() not in response.body and b'"input"' not in response.body
