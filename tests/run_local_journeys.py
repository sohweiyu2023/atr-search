"""Local-only CLI + isolated headless Chrome acceptance harness.

Uses synthetic feeds/articles and the candidate's real Pagefind output. It does not
test the separately owned Blogger widget or native keyboard input. No downloads,
credentials, user browser profile, external pages or hosted CI are needed.
"""
import argparse
import datetime as dt
import functools
import hashlib
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import html
import json
from pathlib import Path
import subprocess
import sys
import threading
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from status_page import render_status


PROBE = '''<script>
addEventListener('load',()=>{const links=[...document.querySelectorAll('nav a')];
const initialState=document.getElementById('freshness').dataset.state,originalNow=Date.now;
Date.now=()=>originalNow()+13*60*60*1000;dispatchEvent(new Event('focus'));
const elapsedFocusState=document.getElementById('freshness').dataset.state;
Date.now=originalNow;dispatchEvent(new Event('focus'));
const result={state:document.getElementById('freshness').dataset.state,
 elapsedFocusState,clockRestored:document.getElementById('freshness').dataset.state===initialState,
 width:innerWidth,overflow:document.documentElement.scrollWidth>innerWidth,
 headings:[...document.querySelectorAll('h1,h2')].map(n=>n.textContent),
 links:links.map(a=>({text:a.textContent,href:a.getAttribute('href'),visible:a.getBoundingClientRect().width>0})),
 focusable:links.every(a=>{a.focus();return document.activeElement===a;}),
 privateDataPresent:document.body.innerText.includes('PRIVATE_TEST_CANARY')};
const recovery=document.querySelectorAll('h2')[1];recovery.scrollIntoView();result.recoveryReachable=recovery.getBoundingClientRect().top<innerHeight;scrollTo(0,0);
const node=document.createElement('script');node.id='qa-result';node.type='application/json';node.textContent=JSON.stringify(result);document.body.append(node);});
</script>'''

ENGINE = '''<!doctype html><meta charset="utf-8"><title>Synthetic Pagefind API journey</title>
<h1>Synthetic search-engine contract, not the Blogger widget</h1><pre id="out">Loading</pre>
<img src="/hold-engine" alt="" hidden><iframe name="article" id="article"></iframe><script type="module">
const result={checks:[],synthetic:true,widgetTested:false};
try{
 const cases=[['en','pagefind','quiet mind','Synthetic English article'],['zh','pagefind-zh','缘起','合成中文文章'],['ja','pagefind-ja','悟り','合成日本語記事']];
 for(const [lang,dir,query,title] of cases){
  document.getElementById('out').textContent='import-'+lang;
  const engine=await import('/public/'+dir+'/pagefind.js');
  document.getElementById('out').textContent='search-'+lang;
  const hits=await engine.search(query);if(!hits.results.length)throw Error(lang+' no matching result');
  const data=await hits.results[0].data();if(data.meta.title!==title)throw Error(lang+' title mismatch');
  const link=new URL(data.meta.link);if(link.origin!==location.origin)throw Error('external destination rejected');
  const article=await fetch(link.href).then(r=>r.text());if(!article.includes(title))throw Error('destination missing title');
  const again=await engine.search(query);if(again.results.length!==hits.results.length)throw Error('repeat unstable');
  const empty=await engine.search('zzsyntheticmissingwordzz');if(empty.results.length)throw Error('empty mismatch');
  result.checks.push({lang,matches:hits.results.length,title,destination:link.pathname,repeat:true,noMatch:true});
 }
 const frame=document.getElementById('article');const a=document.createElement('a');a.href='/article/en.html';a.target='article';a.textContent='Open synthetic article';document.body.append(a);
 await new Promise((resolve,reject)=>{frame.onload=()=>{try{if(!frame.contentDocument.querySelector('h1').textContent.includes('Synthetic English'))throw Error('article navigation');resolve();}catch(e){reject(e);}};a.click();});
 result.articleNavigation=true;
 try{await import('/missing-index/pagefind.js');result.missingIndexError=false;}catch{result.missingIndexError=true;}
 result.passed=true;
}catch(error){result.passed=false;result.error=String(error);}
document.getElementById('out').textContent=JSON.stringify(result);const node=document.createElement('script');node.id='qa-result';node.type='application/json';node.textContent=JSON.stringify(result);document.body.append(node);fetch('/release-engine');
</script>'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--chrome', required=True, help='existing installed Chrome binary; no install performed')
    parser.add_argument('--out', default=str(ROOT / '.diagnostics' / ('journeys-' + uuid.uuid4().hex[:8])))
    args = parser.parse_args()
    runtime_sources=('build_index.py','diagnostics.py','status_page.py','output_publish.py','safe_evidence.py','tests/run_local_journeys.py')
    start_hashes={name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in runtime_sources}
    root = Path(args.out).resolve()
    root.mkdir(parents=True, exist_ok=False)
    engine_done = threading.Event()
    class Handler(SimpleHTTPRequestHandler):
        def log_message(self, *args): pass  # Do not create request/query telemetry.
        def do_GET(self):
            if self.path == '/hold-engine':
                engine_done.wait(20)
                self.send_response(204);self.end_headers();return
            if self.path == '/release-engine':
                engine_done.set();self.send_response(204);self.end_headers();return
            if self.path.startswith('/public/index.html') or self.path in ('/stale.html', '/unknown.html'):
                path = root / self.path.lstrip('/').split('?')[0]
                body = path.read_text(encoding='utf-8').replace('</body>', PROBE + '</body>').encode()
                self.send_response(200); self.send_header('Content-Type', 'text/html; charset=utf-8');self.end_headers();self.wfile.write(body)
            else: super().do_GET()
    server = ThreadingHTTPServer(('127.0.0.1', 0), functools.partial(Handler, directory=str(root)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    origin = 'http://127.0.0.1:' + str(server.server_port)
    evidence = {'synthetic':True,'sharedForegroundUsed':False,'widgetTested':False,'checks':[]}
    cli_count = 0
    def run_cli(feed, expected, injection=None, extra=()):
        nonlocal cli_count
        command=[sys.executable,str(ROOT/'build_index.py'),'--feed-file',str(feed),'--out',str(root/'public'),
                 '--work',str(root/'work'),'--diagnostics',str(root/'private/build.json')]
        command.extend(extra)
        if injection:
            program='import sys;from pathlib import Path;import build_index as b;'+injection+';sys.argv=["build_index.py"]+sys.argv[1:];sys.exit(b.main())'
            command=[sys.executable,'-c',program]+command[2:]
        run=subprocess.run(command,cwd=ROOT,capture_output=True,text=True,timeout=45)
        cli_count += 1
        (root/('cli-'+str(cli_count)+'.stdout.txt')).write_text(run.stdout,encoding='utf-8')
        (root/('cli-'+str(cli_count)+'.stderr.txt')).write_text(run.stderr,encoding='utf-8')
        if run.returncode != expected: raise AssertionError('CLI exit '+str(run.returncode)+' expected '+str(expected))
        return run
    try:
        help_run=subprocess.run([sys.executable,str(ROOT/'build_index.py'),'--help'],capture_output=True,text=True,timeout=10)
        assert help_run.returncode==0 and '--feed-file' in help_run.stdout
        def entry(lang,title,text):
            return {'link':[{'rel':'alternate','href':origin+'/article/'+lang+'.html'}],
                    'title':{'$t':title},'content':{'$t':'<p>'+text+'</p>'},'published':{'$t':'2026-11-04T00:00:00Z'}}
        records=[entry('en','Synthetic English article','quiet mind synthetic search fixture'),
                 entry('zh','合成中文文章','缘起性空的中文合成测试。'*30),entry('ja','合成日本語記事','これは悟りの七段階のための合成テストです。'*30)]
        (root/'article').mkdir()
        for lang,record in zip(('en','zh','ja'),records):
            (root/'article'/ (lang+'.html')).write_text('<!doctype html><meta charset="utf-8"><h1>'+html.escape(record['title']['$t'])+'</h1>',encoding='utf-8')
        feed=root/'feed.json';feed.write_text(json.dumps({'feed':{'entry':records}},ensure_ascii=False),encoding='utf-8')
        run_cli(feed,0)
        status=json.loads((root/'public/status.json').read_text())
        assert (status['posts'],status['chinese'],status['japanese'])==(3,1,1)
        assert not (root/'public/evidence').exists()
        previous=(root/'public/status.json').read_bytes()
        work_note=root/'work/operator-note.txt';work_note.write_text('preserve owned work note')
        unmanaged=root/'unmanaged-source';unmanaged.mkdir();(unmanaged/'source.txt').write_text('preserve unrelated source')
        rejected=run_cli(feed,2,extra=['--work',str(unmanaged)])
        assert 'not marked as generated' in rejected.stdout
        assert (unmanaged/'source.txt').read_text()=='preserve unrelated source'
        overlap=run_cli(feed,2,extra=['--diagnostics',str(root/'public/invalid-private.json')])
        assert 'Choose separate generated output' in overlap.stdout
        assert not (root/'public/invalid-private.json').exists()
        assert (root/'public/status.json').read_bytes()==previous
        bad=root/'empty.json';bad.write_text('{"feed":{"entry":[]}}')
        run_cli(bad,1);assert (root/'public/status.json').read_bytes()==previous
        bad.write_text('{malformed');run_cli(bad,1);assert (root/'public/status.json').read_bytes()==previous
        setup=run_cli(feed,1,'b.PAGEFIND=Path("synthetic-missing-pagefind")')
        assert 'Setup required: run npm ci --ignore-scripts' in setup.stdout
        assert (root/'public/status.json').read_bytes()==previous
        run_cli(feed,130,'b.run_bounded=lambda *a,**k: (_ for _ in ()).throw(KeyboardInterrupt())')
        assert json.loads((root/'private/build.json').read_text())['outcome']=='cancelled'
        assert (root/'public/status.json').read_bytes()==previous
        old_generation=json.loads((root/'public/.atr-search-output.json').read_text())['generation']
        commit_injection='import output_publish as p;original_replace=p.os.replace;p.os.replace=lambda s,d: (original_replace(s,d),(_ for _ in ()).throw(KeyboardInterrupt()) if Path(s).name.startswith(".atr-search-stage-") else None)[0]'
        committed=run_cli(feed,130,commit_injection)
        assert 'Complete replacement output was observed' in committed.stdout
        assert json.loads((root/'public/.atr-search-output.json').read_text())['generation']!=old_generation
        run_cli(feed,0)
        assert list(root.glob('.atr-search-output-backup-*'))
        status=json.loads((root/'public/status.json').read_text())
        assert work_note.read_text()=='preserve owned work note'
        evidence['checks'].append({'cliHelp':True,'buildAndRepeat':True,'emptyAndMalformedPreserveLastGood':True,
                                  'missingDependencyExplained':True,'unsafePathsRejectedBeforeWriting':True,'injectedCancellationPreservesLastGood':True,
                                  'unownedSourcePreserved':True,'ownedWorkNotePreserved':True,
                                  'injectedPostCommitInterruptionReconciled':True,'versionedOutputBackup':True,'status':status})
        for label,built in [('stale','2000-01-01T00:00:00Z'),('unknown','not-a-date')]:
            fake=dict(status,built=built);(root/(label+'.html')).write_text(render_status(fake),encoding='utf-8')
        (root/'engine.html').write_text(ENGINE,encoding='utf-8')
        (root/'mobile-host.html').write_text('''<!doctype html><meta charset="utf-8"><style>body{margin:0}iframe{border:0;width:390px;height:844px;display:block}</style><iframe src="/public/index.html?frame=1"></iframe><script>document.querySelector('iframe').onload=function(){const source=this.contentDocument.getElementById('qa-result');if(source){const node=document.createElement('script');node.id='qa-result';node.type='application/json';node.textContent=source.textContent;document.body.append(node);}};</script>''',encoding='utf-8')
        def browser(path,label,width,height,screenshot=False):
            profile=root/('profile-'+label)
            command=[args.chrome,'--headless=new','--disable-background-networking','--disable-sync','--disable-extensions',
                     '--disable-component-update','--no-first-run','--no-default-browser-check','--no-proxy-server',
                     '--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1',
                     '--user-data-dir='+str(profile),'--window-size='+str(width)+','+str(height),
                     '--virtual-time-budget=12000','--dump-dom']
            if screenshot: command.append('--screenshot='+str(root/(label+'.png')))
            command.append(origin+path)
            run=subprocess.run(command,capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=35)
            (root/(label+'-browser.stderr.txt')).write_text(run.stderr,encoding='utf-8')
            (root/(label+'.dom.html')).write_text(run.stdout,encoding='utf-8')
            if run.returncode: raise AssertionError('Headless browser failed: '+label+' exit '+str(run.returncode))
            marker='<script id="qa-result" type="application/json">'
            if marker not in run.stdout: raise AssertionError('Headless result missing: '+label)
            result=json.loads(html.unescape(run.stdout.split(marker,1)[1].split('</script>',1)[0]))
            evidence['checks'].append({label:result})
            return result
        for label,width,height in [('desktop',1280,900),('mobile',390,844)]:
            result=browser('/mobile-host.html' if label=='mobile' else '/public/index.html',label,width,height,True)
            assert result['state']=='recent' and not result['overflow'] and result['focusable']
            assert result['elapsedFocusState']=='stale' and result['clockRestored']
            assert not result['privateDataPresent'] and result['recoveryReachable']
            if label=='mobile': assert result['width']==390
            assert len(result['links'])==3 and all(a['visible'] for a in result['links'])
        assert browser('/stale.html','stale',390,844)['state']=='stale'
        assert browser('/unknown.html','unknown',390,844)['state']=='unknown'
        engine=browser('/engine.html','engine',1280,900)
        assert engine.get('passed') and engine.get('articleNavigation') and engine.get('missingIndexError')
        evidence['passed']=True
    except Exception as error:
        evidence['passed']=False;evidence['error']=str(error)
    finally:
        server.shutdown();server.server_close()
        evidence['limits']=['Synthetic local browser/runtime only, no live provider or Blogger widget pass.',
                            'Programmatic focus/activation and DOM order; native Tab/Enter, screen-reader and actual download interaction not verified.',
                            'External blog/update-history links inspected but never followed.']
        evidence['source_hashes']=start_hashes
        evidence['source_unchanged_during_run']=all(hashlib.sha256((ROOT/name).read_bytes()).hexdigest()==value for name,value in start_hashes.items())
        if not evidence['source_unchanged_during_run']:
            evidence['passed']=False;evidence['error']='Candidate changed during journey execution'
        (root/'journey-results.json').write_text(json.dumps(evidence,indent=2,ensure_ascii=False),encoding='utf-8')
        print(json.dumps({'passed':evidence.get('passed'),'error':evidence.get('error'),'evidence':str(root/'journey-results.json')}))
    return 0 if evidence.get('passed') else 1


if __name__ == '__main__':
    sys.exit(main())
