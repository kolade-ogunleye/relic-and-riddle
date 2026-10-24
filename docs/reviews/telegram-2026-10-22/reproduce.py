import sys, os, json, tempfile, time
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, os.getcwd())
import requests
from src.db import PipelineDatabase
from src.telegram_bot import TelegramApprovalBot
from src import control, accounts
from src.credits import save_credits, load_credits
from src.telegram_presentation import format_payload
from PIL import Image
results={}
with tempfile.TemporaryDirectory(prefix='relic-telegram-audit-') as scratch, patch.object(requests,'post',side_effect=AssertionError('Network forbidden')), patch.object(requests,'get',side_effect=AssertionError('Network forbidden')):
    root=Path(scratch)
    bank=root/'bank.json'; bank.write_text(json.dumps({'topics':[{'id':'topic','title':'Topic'}]}))
    db=PipelineDatabase(db_path=root/'db.sqlite',topic_bank_file=bank)
    bot=TelegramApprovalBot(db=db,bot_token='123:offline',chat_id='555',approver_ids={'555'})
    calls=[]
    def api(method,**kwargs):
        calls.append((method,kwargs.get('data',{})))
        return {'message_id':300}
    bot._api=api
    db.create_run('review','topic'); db.record_approval('review','topic',101); db.record_approval('review','topic',202)
    bot._handle_callback({'id':'cq','data':'approve:'+db.run_key('review'),'from':{'id':555},'message':{'message_id':101,'chat':{'id':555}}})
    results['old_review_message_can_approve_new_request']={'latest_message_id':202,'clicked_message_id':101,'decision':db.get_approval('review')['decision']}
    assets=root/'assets'; td=assets/'topic'; td.mkdir(parents=True)
    images=[]
    for i in range(control.MIN_SCENE_IMAGES+1):
        name=f'{i}.jpg'; Image.new('RGB',(20,20)).save(td/name)
        images.append({'file':name,'title':name,'license':'PD','source_url':'https://example.org','license_url':'https://example.org/rights'})
    save_credits(td,{'images':images[:-1],'reviewed':False})
    captured=[]
    handler=control.make_command_handler(db,lambda:None,lambda:'started',assets_dir=assets,send_photo=lambda *a,**kw:captured.append(a) or True)
    with patch.object(control,'review_caption',return_value='original review'),patch.object(control,'write_contact_sheet',return_value=None):
        handler('review','topic')
        save_credits(td,{'images':images,'reviewed':False})
        target=captured[-1][2][0]['callback_data'].partition(':')[2]
        reply=handler('imgok',target)
    results['old_image_review_approves_changed_set']={'shown':len(images)-1,'approved':len(load_credits(td)['images']),'reviewed':load_credits(td)['reviewed'],'callback_target':target}
    outside=root/'outside'; outside.mkdir(); save_credits(outside,{'images':[],'reviewed':False})
    results['topic_path_escape']=control._resolve_topic_dir('../outside',assets,db).resolve()==outside.resolve()
    old_cwd=Path.cwd(); os.chdir(root)
    try:
        results['missing_cover_reports_success']=handler('covlock','missing-run:b')
        run_dir=root/'output'/('run_'+('long_topic_'*7)+'20261022'); run_dir.mkdir(parents=True)
        Image.new('RGB',(20,20)).save(run_dir/'thumbnail_variant_b_intrigue.jpg')
        handler('covers','')
        lengths=[len(b['callback_data'].encode()) for row in captured[-1][2] for b in row]
        results['cover_callback_bytes']={'maximum':max(lengths),'telegram_limit':64}
    finally: os.chdir(old_cwd)
    db.create_run('old','topic'); db.create_run('active','topic'); db.set_active_progress('active','Active',2,5,message_id=77)
    bot.finish_progress_card(66,'Old','old',success=False,error='old error')
    results['old_finish_clears_other_run_progress']=db.get_active_progress() is None
    calls.clear(); bot.edit_message_text=lambda *a,**kw:False
    db.set_active_progress('active','Active',2,5,message_id=77)
    bot.update_progress_card(77,3,5,'Active','active')
    results['transient_edit_failure_creates_new_progress_message']=any(m=='sendMessage' for m,_ in calls)
    db.create_run('no-preview','topic'); calls.clear()
    bot._send_review_media=lambda *a:(_ for _ in ()).throw(RuntimeError('preview upload failed'))
    mid=bot.send_approval_request(root/'missing.mp4',root/'missing.jpg',{'topic_id':'topic'},'no-preview')
    results['approval_available_after_preview_failure']={'message_id':mid,'approval_button':any('approve:' in d.get('reply_markup','') for m,d in calls)}
    results['unchecked_only_publisher_has_no_problem']=accounts.problems({'upload_post':{'configured':False},'meta':{'configured':True,'valid':None,'error':'Timeout'}})
    album={'media':json.dumps([{'type':'photo','media':'x','caption':'<b><u>REVIEW</u></b>\nBody','parse_mode':'HTML'}])}
    results['album_caption_bypasses_shared_format']='<u>' in json.loads(format_payload('sendMediaGroup',album)['media'])[0]['caption']
    seen=[]
    update={'update_id':10,'message':{'message_id':99,'date':int(time.time()),'text':'/run','chat':{'id':555},'from':{'id':555}}}
    for _ in range(2):
        fresh=TelegramApprovalBot(db=db,bot_token='123:offline',chat_id='555',approver_ids={'555'})
        fresh._api=lambda m,**kw:[update] if m=='getUpdates' else {'message_id':301}
        fresh.poll_once(lambda command,args:seen.append(command) or '')
    results['same_run_command_after_restart_dispatches']=seen
    db.record_topic_status('topic','in_production')
    handler('qdrop','topic')
    results['old_queue_drop_archives_producing_topic']=db.get_topic_by_id('topic')['status']
    from main import PipelineCoordinator
    from types import SimpleNamespace
    from src import release
    db.create_run('edit-race','topic'); db.update_run('edit-race','pending_approval',script_payload={'caption':'original'},master_video_path=root/'master.mp4')
    db.record_approval('edit-race','topic',400)
    coordinator=PipelineCoordinator.__new__(PipelineCoordinator); coordinator.db=db
    coordinator.topic_engine=SimpleNamespace(get_topic_by_id=db.get_topic_by_id)
    coordinator.telegram_bot=SimpleNamespace(_strip_buttons=lambda *a:None,send_review_card=lambda *a:None)
    coordinator._verify_final_copy=lambda *a: db.update_approval_decision('edit-race','approved',decided_by='concurrent-old-card')
    with patch.object(release,'write_release_report',return_value={'checks':[]}):
        coordinator.edit_copy('edit-race','caption','changed during approval')
    results['concurrent_copy_edit_keeps_approval_for_changed_payload']={'caption':db.get_run('edit-race')['script_payload']['caption'],'decision':db.get_approval('edit-race')['decision']}
    db.create_run('published-cover','topic'); db.update_run('published-cover','published')
    os.chdir(root)
    try:
        published=root/'output'/'published-cover'; published.mkdir(parents=True)
        Image.new('RGB',(20,20),(255,0,0)).save(published/'thumbnail_variant_a_blueprint.jpg')
        old_cover=published/'thumbnail_cover.jpg'; Image.new('RGB',(20,20),(0,0,255)).save(old_cover)
        before=old_cover.read_bytes(); reply=handler('covlock','published-cover:a')
        results['published_cover_can_be_rewritten']={'changed':before!=old_cover.read_bytes(),'run_status':db.get_run('published-cover')['status'],'reported_success':reply.startswith('✅')}
    finally: os.chdir(old_cwd)
    results['image_cancel_claim_disagrees_with_state']={'reply':handler('imgcancel','topic'),'actual_reviewed':load_credits(td)['reviewed']}
    results['missing_audits_still_show_integrity_pass']='100% (Pass' in TelegramApprovalBot.format_card_text({},'unknown')
    from src import probe, free_llm
    db.create_run('run_context_example','topic')
    quoted='Why?\n\n[Context from replied message]: Run ID (for the command line): run_context_example'
    with patch.object(free_llm,'ready',return_value=False):
        answer=probe.probe_system(quoted,db)
    results['replied_run_id_without_buttons_loses_context']='No run was selected' in answer
print(json.dumps(results,indent=2))
