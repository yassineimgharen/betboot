import httpx
import pytest
from betboot.config import Settings
from betboot.providers import isports
from betboot.providers.base import ProviderError
from betboot.research import ResearchProviders


@pytest.fixture(autouse=True)
def reset_spacing():
    isports._last_request.clear()
    yield
    isports._last_request.clear()


def event(fixture):
    return dict(matchId='123', leagueName='English Premier League', status=0,
                matchTime=int(fixture.kickoff.timestamp()), homeName=fixture.home_name,
                awayName=fixture.away_name, homeId='1', awayId='2')


async def test_context_alternative_matches_provenance_and_caches(fixture):
    calls=[]
    lineup={side:[{'playerId':str(n), 'name':f'Test player {n}'} for n in range(11)] for side in ('homeLineup','awayLineup')}
    def response(request):
        calls.append(request)
        path=request.url.path
        rows=([event(fixture)] if path.endswith('basic') else
              [{'matchId':'123', 'teamId':'1', 'name':'Test injured player','reason':'injury'}] if path.endswith('injury') else
              [{'matchId':'123', **lineup}])
        return httpx.Response(200,json={'code':0,'data':rows})
    async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as client:
        provider=ResearchProviders(client,Settings(isports_api_key='test-secret',_env_file=None))
        evidence,cross,gaps=await provider.context(fixture)
        assert cross and not gaps and {r['kind'] for r in evidence}=={'fixture','injuries','lineups'}
        assert 'test-secret' not in str(evidence)
        assert 'confirmation not verified' in evidence[-1]['verification']
        await provider.context(fixture)
        assert len(calls)==3
        assert all(r.url.scheme=='https' for r in calls)


@pytest.mark.parametrize('change',[{'status':-1},{'homeName':'Other club'},{'leagueName':'Other league'},{'matchTime':0},{'neutral':True}])
def test_disagreement_rejected(fixture,change):
    assert isports.agrees(event(fixture),fixture)
    assert not isports.agrees({**event(fixture),**change},fixture)


async def test_empty_context_not_injury_clearance(fixture):
    def response(request):
        return httpx.Response(200,json={'code':0,'data':[event(fixture)] if request.url.path.endswith('basic') else []})
    async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as client:
        provider=ResearchProviders(client,Settings(isports_api_key='test-secret',_env_file=None))
        evidence,cross,gaps=await provider.context(fixture,deep=True)
        assert cross and len(evidence)==1 and len(gaps)==2
        assert all('does not prove absence' in gap for gap in gaps)


async def test_entitlement_error_sanitized_and_cached(fixture):
    calls=[]
    def response(request):
        calls.append(request)
        return httpx.Response(200,json={'code':123,'message':'secret echoed test-secret'})
    async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as client:
        provider=ResearchProviders(client,Settings(isports_api_key='test-secret',_env_file=None))
        for _ in range(2):
            with pytest.raises(ProviderError,match='entitlement') as error:
                await provider.context(fixture)
            assert 'test-secret' not in str(error.value)
        assert len(calls)==1


async def test_endpoint_spacing(monkeypatch):
    clock=[100.0]; sleeps=[]
    monkeypatch.setattr(isports.time,'monotonic',lambda:clock[0])
    async def sleep(seconds):
        sleeps.append(seconds); clock[0]+=seconds
    monkeypatch.setattr(isports.asyncio,'sleep',sleep)
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r:httpx.Response(200,json={'code':0,'data':[]}))) as client:
        p=isports.ISportsContext(client,Settings(isports_api_key='test',_env_file=None))
        await p.fetch('schedule/basic',{'date':'2026-10-24'})
        await p.fetch('schedule/basic',{'date':'2026-10-25'})
    assert sum(sleeps)==90 and max(sleeps)<=45
