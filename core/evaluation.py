"""Frozen local history + blind human labels. Never touches delivery state."""
import csv
import hashlib
import io
import json
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone, date
from pathlib import Path
from uuid import uuid4

from core.database import now
from core.errors import InsightError
from core.profile import Profile


def root(cfg):
    from core.pipeline import database_path
    return database_path(cfg).parent / 'evaluations'


def folder(cfg, name):
    base=root(cfg).resolve()
    target=(base/name).resolve()
    if target.parent!=base or not target.is_dir():
        raise InsightError('evaluation_invalid','只能使用当前数据目录下的评估批次名')
    return target


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def freeze(cfg, db, profile, count=40):
    # Board round robin, stable SHA order within each board.
    strata=defaultdict(list)
    for row in db.db.execute("SELECT * FROM notices WHERE source='byr'"):
        strata[(row['source'], row['origin'])].append(dict(row))
    for group in strata.values():group.sort(key=lambda r:sha(r['id'].encode()))
    selected=[]
    while strata and len(selected)<max(1,min(1000,count)):
        for key in sorted(list(strata)):
            selected.append(strata[key].pop(0))
            if not strata[key]:del strata[key]
            if len(selected)>=max(1,min(1000,count)):break
    if not selected:raise InsightError('evaluation_empty','没有可冻结的真实校园历史记录')
    name=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')+'-'+uuid4().hex[:8]
    dest=root(cfg)/name;dest.mkdir(parents=True,exist_ok=False)
    samples=[{'sample':f'n{i+1:03}', 'split':'test' if i%3==0 else 'calibration', 'id':r['id'],
              'hash':r['hash'],'revision':r['revision'],'payload':json.loads(r['payload'])} for i,r in enumerate(selected)]
    data={'created_at':now(),'date':datetime.now(timezone.utc).astimezone().date().isoformat(),
          'profile':profile.model_dump(),'profile_hash':profile.fingerprint(),'samples':samples}
    raw=json.dumps(data,ensure_ascii=False,sort_keys=True).encode()
    (dest/'dataset.json').write_bytes(raw)
    (dest/'manifest.json').write_text(json.dumps({'sha256':sha(raw),'n':len(samples),'scope':dest.parent.parent.name}),encoding='utf-8')
    with (dest/'labels.csv').open('w',newline='',encoding='utf-8-sig') as f:
        writer=csv.writer(f);writer.writerow(['sample','label','explanation_quality'])
        writer.writerows((s['sample'],'','') for s in samples)
    # Predictions deliberately absent from annotation view to reduce anchoring.
    lines=['# 本地历史盲标清单','原文可能私有；不要上传或提交Git。先核对冻结画像dataset.json。',
           'labels.csv标签：important / relevant / irrelevant / unsure；评分可留空。','标题/正文均是已有历史，不代表现在仍可报名。']
    for sample in samples:
        p=sample['payload']
        # Render scraped text literally: don't turn a malicious Markdown image
        # into a remote request that could leak viewing/private identifiers.
        def literal(value):
            return '\n'.join('    '+line for line in str(value).splitlines())
        lines.extend([f"\n## {sample['sample']} · {p['source']}",literal(p.get('channel','')),literal(p['title']),
                      literal(f"发布日期：{p.get('published','未知')}"),literal(p.get('body','') or '正文缺失，请打开本人原通知确认。')])
    (dest/'review.md').write_text('\n\n'.join(lines),encoding='utf-8')
    return {'batch':name,'n':len(samples),'path':str(dest),'dataset_sha256':sha(raw),'labels':0}


def read(cfg,name):
    dest=folder(cfg,name)
    raw=(dest/'dataset.json').read_bytes()
    meta=json.loads((dest/'manifest.json').read_text(encoding='utf-8'))
    if meta['sha256']!=sha(raw) or meta['scope']!=dest.parent.parent.name:
        raise InsightError('evaluation_invalid','评估快照哈希或账号作用域不符')
    data=json.loads(raw)
    if Profile.model_validate(data['profile']).fingerprint()!=data['profile_hash']:
        raise InsightError('evaluation_invalid','冻结画像指纹不符')
    return dest,data


def labels(dest, samples):
    valid={s['sample'] for s in samples}; result={}; seen=set()
    from core.feedback import LABELS
    with (dest/'labels.csv').open(newline='',encoding='utf-8-sig') as f:
        for row in csv.DictReader(f):
            code=row['sample'];label=row['label'].strip()
            if code not in valid or code in seen:raise InsightError('evaluation_invalid','标注编号无效或重复')
            seen.add(code)
            if not label:continue
            if label not in LABELS:raise InsightError('evaluation_invalid','标注仅允许important/relevant/irrelevant/unsure')
            score=row.get('explanation_quality','').strip()
            if score and score not in ('1','2','3','4','5'):raise InsightError('evaluation_invalid','解释评分应为1至5或空')
            result[code]={'label':label,'explanation_quality':int(score) if score else None}
    return result


def label(cfg,name,sample,value):
    from core.feedback import LABELS
    dest,data=read(cfg,name)
    if value not in LABELS or sample not in {s['sample'] for s in data['samples']}:
        raise InsightError('evaluation_invalid','请指定批次中的样本编号及有效标签')
    file=dest/'labels.csv'
    with file.open(newline='',encoding='utf-8-sig') as f:rows=list(csv.DictReader(f))
    for row in rows:
        if row['sample']==sample:row['label']=value
    temporary=file.with_suffix('.csv.tmp')
    with temporary.open('w',newline='',encoding='utf-8-sig') as f:
        writer=csv.DictWriter(f,fieldnames=['sample','label','explanation_quality']);writer.writeheader();writer.writerows(rows)
    temporary.replace(file)
    return {'status':'labeled','sample':sample,'production_feedback_modified':False}


def reason_hash(prediction):
    return sha(json.dumps(prediction.get('reasons',[]),ensure_ascii=False,sort_keys=True).encode())


def rate(cfg,name,sample,method,score):
    dest,data=read(cfg,name)
    if method not in ('legacy','interest','api') or type(score) is not int or not 0<=score<=5:
        raise InsightError('evaluation_invalid','解释评分需要具体方法及0至5分')
    report=json.loads((dest/'report-all.json').read_text(encoding='utf-8'))
    predictions=report['methods'].get(method,{}).get('predictions',[])
    pred=next((p for p in predictions if p['sample']==sample and p['status']=='ok'),None)
    if pred is None:raise InsightError('evaluation_invalid','该方法没有可评分的实际解释')
    file=dest/'explanation-ratings.json'
    rows=json.loads(file.read_text(encoding='utf-8')) if file.exists() else []
    rows=[r for r in rows if (r['sample'],r['method'])!=(sample,method)]
    rows.append({'sample':sample,'method':method,'score':score,'reason_hash':reason_hash(pred),'created_at':now()})
    tmp=file.with_suffix('.json.tmp');tmp.write_text(json.dumps(rows),encoding='utf-8');tmp.replace(file)
    return {'status':'rated','sample':sample,'method':method,'score':score}


def metrics(predictions, ground):
    rows=[p for p in predictions if p['sample'] in ground and ground[p['sample']]['label']!='unsure' and p['status']=='ok']
    important=[p for p in rows if ground[p['sample']]['label']=='important']
    positive=[p for p in rows if ground[p['sample']]['label'] in ('important','relevant')]
    selected=[p for p in rows if p['route'] in ('immediate','digest')]
    negative=[p for p in rows if ground[p['sample']]['label']=='irrelevant']
    ratio=lambda a,b:a/b if b else None
    return {'labeled_evaluable':len(rows), 'important_n':len(important), 'irrelevant_n':len(negative),
        'important_recall':ratio(sum(p['route']!='archive' for p in important),len(important)),
        'positive_rank_recall':ratio(sum(p['relevance'] in ('high','medium') for p in positive),len(positive)),
        'irrelevant_reminder_ratio':ratio(sum(ground[p['sample']]['label']=='irrelevant' for p in selected),len(selected)),
        'irrelevant_rank_ratio':ratio(sum(ground[p['sample']]['label']=='irrelevant' and p['relevance'] in ('high','medium') for p in rows),sum(p['relevance'] in ('high','medium') for p in rows)),
        'archive_rate':ratio(sum(p['route']=='archive' for p in rows),len(rows)),
        'automatic_evidence_coverage':ratio(sum(bool(p.get('evidence')) for p in rows),len(rows)),
        'explanation_human_mean':None, 'explanation_note':'盲标表不评分未展示的解释；方法报告需另外真人评审。'}


def evaluate(cfg,name,method='all',client=None):
    from core.analysis import rules, analyze_row, settings, is_local
    from core.database import Database
    from core.smart import route
    from core.personalization import VERSION, decide
    dest,data=read(cfg,name); ground=labels(dest,data['samples'])
    profile=Profile.model_validate(data['profile'])
    legacy=profile.model_copy(update={'interests':[]})
    reports={}; today=date.fromisoformat(data['date'])
    methods=['legacy','interest'] if method=='all' else [method]
    for engine in methods:
        start=time.perf_counter();predictions=[];calls=0
        options=settings(cfg); api_db=None
        budget=max(.1,min(60,float(options.get('budget_seconds',12))))
        if engine=='api':
            if not options.get('base_url') or not options.get('model'):
                reports['api']={'status':'not_configured','metrics':None,'actual_model_successes':0};continue
            options['mode']='api';api_db=Database(dest/'api-cache.sqlite3')
        try:
            for sample in data['samples']:
                item=sample['payload']; status='ok'
                if engine=='api':
                    remaining=budget-(time.perf_counter()-start)
                    if remaining<=.1:
                        predictions.append({'sample':sample['sample'],'split':sample['split'],'status':'budget_exhausted'});continue
                    from core.schema import Item
                    from core.database import identity
                    it=Item(**item);api_db.upsert(it)
                    row=api_db.db.execute('SELECT * FROM notices WHERE id=?',(identity(it),)).fetchone()
                    outcome=analyze_row(api_db,row,profile,options,min(8,remaining),client)
                    calls+=int(not outcome['cached'])
                    cached=api_db.db.execute('SELECT result FROM analysis_cache WHERE notice_id=? ORDER BY created_at DESC LIMIT 1',(row['id'],)).fetchone()
                    from core.analysis import Analysis
                    value=Analysis.model_validate_json(cached[0])
                    status='ok' if outcome['engine']=='api' else 'fallback_not_ai'
                else:
                    value=rules(item, legacy if engine=='legacy' else profile)
                predicted={'sample':sample['sample'],'split':sample['split'],'status':status,'relevance':value.relevance,
                           'route':route(item,value,'ok' if status=='ok' else 'fallback',profile,today),
                           'evidence':[r.evidence for r in value.reasons if r.evidence],
                           'reasons':[r.model_dump() for r in value.reasons]}
                predictions.append(predicted)
        finally:
            if api_db:api_db.close()
        reports[engine]={'status':'evaluated' if ground else 'labels_pending',
            'prediction_counts':dict(Counter(p['status'] for p in predictions)),
            'relevance_counts':dict(Counter(p.get('relevance','unavailable') for p in predictions)),
            'route_counts':dict(Counter(p.get('route','unavailable') for p in predictions)),
            'descriptive_all_metrics':metrics(predictions,ground),
            'test_metrics':metrics([p for p in predictions if p['split']=='test'],ground),
            'calibration_metrics':metrics([p for p in predictions if p['split']=='calibration'],ground),
            'wall_seconds':round(time.perf_counter()-start,4),'top_level_uncached_attempts':calls,
            'monetary_cost':0 if engine!='api' else None,'tokens':None,
            'personalization':('model_profile_local' if is_local(options.get('base_url','')) else ('model_profile_authorized' if options.get('allow_profile_cloud') is True else 'local_override_after_ai')) if engine=='api' else 'local_profile',
            'predictions':predictions}
        rating_file=dest/'explanation-ratings.json'
        ratings=json.loads(rating_file.read_text(encoding='utf-8')) if rating_file.exists() else []
        for key,split in [('descriptive_all_metrics',None),('test_metrics','test'),('calibration_metrics','calibration')]:
            values=[r['score'] for r in ratings if r['method']==engine and any(p['sample']==r['sample'] and (not split or p['split']==split) and reason_hash(p)==r['reason_hash'] for p in predictions)]
            reports[engine][key]['explanation_human_mean']=sum(values)/len(values) if values else None
            reports[engine][key]['explanation_human_n']=len(values)
            reports[engine][key]['explanation_note']='仅统计真人对该方法实际解释的有效评分；不是自动判定的正确率。'
    if method=='all':reports['api']={'status':'not_run_requires_explicit_api_method','metrics':None,'actual_model_successes':0}
    report={'batch':name,'n':len(data['samples']),'profile_hash':data['profile_hash'],
            'dataset_sha256':sha((dest/'dataset.json').read_bytes()),'labels_sha256':sha((dest/'labels.csv').read_bytes()),
            'labels_n':len(ground),'strategy':VERSION,'methods':reports,'delivery_state_modified':False,
            'limitations':['标签不足时效果指标为null，不拿自身预测当标签。',
                'calibration仅用于调试，最终看test；当前方法不训练、不读取生产反馈作为测试特征。',
                'confidence为启发值/模型自报，不是校准后的统计概率。',
                '成本仅记录实测耗时与顶层未缓存尝试数；未知token和云服务计费不猜测。']}
    (dest/('report-'+method+'.json')).write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    # No private excerpts / reasons in command or scheduler output.
    return {**{k:v for k,v in report.items() if k!='methods'},
            'methods':{k:{a:b for a,b in v.items() if a!='predictions'} for k,v in reports.items()},'path':str(dest/('report-'+method+'.json'))}
