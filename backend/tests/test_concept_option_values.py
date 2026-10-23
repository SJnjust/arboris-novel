import json
import pytest
from app.services.concept_service import _ui_control

@pytest.mark.parametrize('value,labels', [('悬疑', ['悬疑']), (7, []), (False, []), ({'label':'悬疑'}, []), (None, []), (['悬疑','奇幻'], ['悬疑','奇幻'])])
def test_control_options_have_safe_types(value, labels):
    result = _ui_control({'type':'single_choice','options':value})
    assert [row['label'] for row in result['options'] or []] == labels
    assert result['type'] == ('single_choice' if labels else 'text_input')

@pytest.mark.parametrize('value', ['悬疑', 7])
async def test_option_values_survive_real_stream_final(client, auth, project, llm, value):
    llm.reply('concept', {'ai_message':'请选择', 'ui_control':{'type':'single_choice','options':value}, 'conversation_state':{}, 'is_complete':False})
    response = await client.post(f'/api/novels/{project}/concept/converse/stream', headers=auth, json={'user_input':{'value':'测试'}, 'conversation_state':{}, 'client_msg_id':'options-value'})
    assert response.status_code == 200
    final = next(json.loads(frame.split('data: ',1)[1]) for frame in response.text.split('\n\n') if frame.startswith('event: final'))
    assert final['ui_control']['type'] == ('single_choice' if isinstance(value,str) else 'text_input')
