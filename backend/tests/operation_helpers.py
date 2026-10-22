"""Assert the 202 contract, then expose the completed task's domain result to old regressions."""
import asyncio
import httpx


async def complete_operation(client, auth, submitted):
    assert submitted.status_code == 202, submitted.text
    job_id = submitted.json()['job_id']
    for _ in range(1000):
        response = await client.get(f'/api/jobs/{job_id}', headers=auth)
        assert response.status_code == 200
        job = response.json()
        if job['status'] == 'succeeded':
            return httpx.Response(200, json=job['result'])
        if job['status'] == 'failed':
            return httpx.Response(job['error']['status_code'], json={'detail': job['error']['detail']})
        assert job['status'] != 'cancelled'
        await asyncio.sleep(0.01)
    raise AssertionError('operation job did not finish')
