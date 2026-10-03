"""Local signup is simple; hosted setup and shared-workspace invitations stay bounded."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
import os
import uuid

import pytest
from fastapi import HTTPException, Response
from starlette.requests import Request

from app.api.v1 import local_auth
from app.models.account_invite import AccountInvite
from scripts import create_setup_token


class Scalar:
    def __init__(self,value):self.value=value
    def scalar_one_or_none(self):return self.value


def req(headers=None):
    return Request({'type':'http','method':'POST','path':'/auth/local/setup','scheme':'http',
                    'headers':[(k.encode(),v.encode()) for k,v in (headers or {}).items()]})


def body():
    return local_auth.AccountRequest(token='synthetic-setup-token',username='owner',password='long-test-password')


def db_with_results(*values):
    db=AsyncMock();db.add=MagicMock();db.execute.side_effect=[Scalar(v) for v in values]
    return db


@pytest.fixture(autouse=True)
def builtin_mode(monkeypatch):
    monkeypatch.setenv('AUTH_MODE','builtin')
    monkeypatch.setenv('FRONTEND_URL','http://localhost:3000')
    monkeypatch.setenv('PUBLIC_URL','http://localhost:3000')
    monkeypatch.setenv('REGISTRATION_MODE','auto')


@pytest.mark.asyncio
async def test_owner_setup_requires_operator_token_before_creating_any_account():
    db=db_with_results(None,None,None)
    with pytest.raises(HTTPException) as exc:
        await local_auth.setup_owner(body(),req(),Response(),SimpleNamespace(db=db))
    assert exc.value.status_code==400
    db.add.assert_not_called();db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_owner_setup_already_completed_never_reopens_even_for_disabled_owner():
    db=db_with_results(None,uuid.uuid4())
    with pytest.raises(HTTPException) as exc:
        await local_auth.setup_owner(body(),req(),Response(),SimpleNamespace(db=db))
    assert exc.value.status_code==409
    db.add.assert_not_called()


@pytest.mark.parametrize('consumed,expired',[(True,False),(False,True)])
@pytest.mark.asyncio
async def test_invite_replay_or_expiry_cannot_create_an_account(consumed,expired):
    now=datetime.now(timezone.utc)
    invite=SimpleNamespace(consumed_at=now if consumed else None,expires_at=now+timedelta(seconds=-1 if expired else 60))
    db=db_with_results(invite)
    with pytest.raises(HTTPException) as exc:
        await local_auth.signup_invited(body(),req(),Response(),SimpleNamespace(db=db))
    assert exc.value.status_code==400
    assert body().token not in str(exc.value.detail)
    db.add.assert_not_called();db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_valid_owner_setup_creates_bounded_owner_and_consumes_capability():
    invite=SimpleNamespace(consumed_at=None,expires_at=datetime.now(timezone.utc)+timedelta(minutes=5))
    db=db_with_results(None,None,invite,None)
    response=Response()
    result=await local_auth.setup_owner(body(),req(),response,SimpleNamespace(db=db))
    assert result['user']['role']=='user' and result['user']['username']=='owner'
    assert invite.consumed_at is not None
    models=[c.args[0] for c in db.add.call_args_list]
    membership=next(m for m in models if m.__class__.__name__=='SpaceMembership')
    assert membership.role=='admin'
    user=next(m for m in models if m.__class__.__name__=='User')
    assert user.auth_provider=='builtin' and user.password_hash!=body().password
    assert local_auth.verify_local_password(body().password,user.password_hash)
    assert 'HttpOnly' in response.headers['set-cookie']
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_invited_member_cannot_issue_another_invitation(monkeypatch):
    user=SimpleNamespace(id=uuid.uuid4(),space_id=uuid.uuid4(),current_space_id=None,auth_provider='builtin')
    monkeypatch.setattr(local_auth,'_resolve_admin_human_user_from_bearer_token',AsyncMock(return_value=user))
    db=db_with_results(SimpleNamespace(role='member'))
    with pytest.raises(HTTPException) as exc:
        await local_auth.create_invite(local_auth.InviteRequest(),req({'authorization':'Bearer synthetic'}),SimpleNamespace(db=db))
    assert exc.value.status_code==403
    db.add.assert_not_called()


@pytest.mark.asyncio
async def test_invitation_creation_requires_authenticated_human():
    db=db_with_results()
    with pytest.raises(HTTPException) as exc:
        await local_auth.create_invite(local_auth.InviteRequest(),req(),SimpleNamespace(db=db))
    assert exc.value.status_code==401


@pytest.mark.asyncio
async def test_operator_setup_token_is_private_hashed_and_not_printed(tmp_path,monkeypatch,capsys):
    db=db_with_results(None,None,None,None)
    manager=AsyncMock();manager.__aenter__.return_value=db
    monkeypatch.setattr(create_setup_token,'AsyncSessionLocal',MagicMock(return_value=manager))
    output=tmp_path/'owner-setup.token'
    await create_setup_token.issue_setup_token(output)
    assert output.stat().st_mode & 0o777==0o600
    token=output.read_text().strip()
    invite=db.add.call_args.args[0]
    assert isinstance(invite,AccountInvite)
    assert invite.token_hash==local_auth.hash_token(token) and invite.token_hash!=token
    assert token not in capsys.readouterr().out
    with pytest.raises(FileExistsError): await create_setup_token.issue_setup_token(output)


@pytest.mark.asyncio
async def test_username_race_returns_generic_conflict_and_rolls_back():
    from sqlalchemy.exc import IntegrityError
    invite=SimpleNamespace(consumed_at=None,expires_at=datetime.now(timezone.utc)+timedelta(minutes=5))
    creator=SimpleNamespace(active=True,auth_provider='builtin',id=uuid.uuid4())
    invite.created_by=creator.id;invite.space_id=uuid.uuid4()
    db=db_with_results(invite,SimpleNamespace(role='admin'),None)
    db.get.return_value=creator
    db.flush.side_effect=IntegrityError('insert users',{'password_hash':'private-test-hash'},Exception('duplicate'))
    with pytest.raises(HTTPException) as exc:
        await local_auth.signup_invited(body(),req(),Response(),SimpleNamespace(db=db))
    assert exc.value.status_code==409 and 'private-test-hash' not in str(exc.value.detail)
    db.rollback.assert_awaited_once();db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_local_first_owner_needs_no_token_and_setup_is_transaction_locked():
    db = db_with_results(None, None, None)
    account = local_auth.AccountRequest(username='owner', password='long-test-password')
    result = await local_auth.setup_owner(account, req(), Response(), SimpleNamespace(db=db))
    assert result['user']['role'] == 'user'
    assert 'pg_advisory_xact_lock' in str(db.execute.call_args_list[0].args[0])
    models = [call.args[0] for call in db.add.call_args_list]
    assert not any(isinstance(model, AccountInvite) for model in models)
    assert next(model for model in models if model.__class__.__name__ == 'SpaceMembership').role == 'admin'
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_hosted_owner_setup_cannot_be_opened_by_forged_loopback_headers(monkeypatch):
    monkeypatch.setenv('PUBLIC_URL', 'https://workspace.example')
    db = db_with_results(None, None)
    with pytest.raises(HTTPException) as exc:
        await local_auth.setup_owner(local_auth.AccountRequest(username='owner', password='long-test-password'),
                                     req({'host': 'localhost:3000', 'x-forwarded-host': 'localhost:3000'}),
                                     Response(), SimpleNamespace(db=db))
    assert exc.value.status_code == 400
    db.add.assert_not_called()


@pytest.mark.asyncio
async def test_open_signup_creates_an_independent_private_workspace():
    db = db_with_results(uuid.uuid4(), None)
    result = await local_auth.signup_invited(local_auth.AccountRequest(username='another', password='long-test-password'),
                                            req(), Response(), SimpleNamespace(db=db))
    models = [call.args[0] for call in db.add.call_args_list]
    space = next(model for model in models if model.__class__.__name__ == 'Space')
    membership = next(model for model in models if model.__class__.__name__ == 'SpaceMembership')
    assert space.visibility == 'private' and str(space.id) == result['space_id']
    assert membership.space_id == space.id and membership.role == 'admin'
    assert result['user']['role'] == 'user'
    db.commit.assert_awaited_once()


@pytest.mark.parametrize('mode', ['invite_only', 'closed'])
@pytest.mark.asyncio
async def test_registration_restrictions_are_enforced_by_the_backend(monkeypatch, mode):
    monkeypatch.setenv('REGISTRATION_MODE', mode)
    db = db_with_results()
    with pytest.raises(HTTPException) as exc:
        await local_auth.signup_invited(local_auth.AccountRequest(username='another', password='long-test-password'),
                                        req(), Response(), SimpleNamespace(db=db))
    assert exc.value.status_code == 403
    db.add.assert_not_called()


@pytest.mark.asyncio
async def test_open_signup_cannot_claim_an_uninitialized_hosted_installation(monkeypatch):
    monkeypatch.setenv('PUBLIC_URL', 'https://workspace.example')
    monkeypatch.setenv('REGISTRATION_MODE', 'open')
    db = db_with_results(None)
    with pytest.raises(HTTPException) as exc:
        await local_auth.signup_invited(local_auth.AccountRequest(username='another', password='long-test-password'),
                                        req(), Response(), SimpleNamespace(db=db))
    assert exc.value.status_code == 409
    db.add.assert_not_called()


@pytest.mark.asyncio
async def test_account_status_reports_registration_and_never_caches_setup():
    response = Response()
    result = await local_auth.account_status(response, SimpleNamespace(db=db_with_results(None)))
    assert result == {'auth_mode': 'builtin', 'setup_required': True, 'setup_flow': 'browser', 'signup': 'open'}
    assert response.headers['cache-control'] == 'no-store'


@pytest.mark.asyncio
async def test_closed_registration_cannot_issue_unusable_invitations(monkeypatch):
    monkeypatch.setenv('REGISTRATION_MODE', 'closed')
    db = db_with_results()
    with pytest.raises(HTTPException) as exc:
        await local_auth.create_invite(local_auth.InviteRequest(), req(), SimpleNamespace(db=db))
    assert exc.value.status_code == 403
    db.add.assert_not_called()


def test_account_creation_requires_a_passphrase_without_imposing_character_rules():
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        local_auth.AccountRequest(username='owner', password='short-password')
    assert local_auth.AccountRequest(username='owner', password='a long easy passphrase').password == 'a long easy passphrase'
