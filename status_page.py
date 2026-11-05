"""Small public last-success page; no provider details, queries, tracking or network fetches."""
import html


def render_status(status):
    built = html.escape(status['built'], quote=True)
    return '''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>AtR search index status</title><style>
body{font:1rem/1.6 system-ui,sans-serif;color:#17212b;background:#fff;margin:0;padding:1rem}
main{max-width:52rem;margin:auto}h1{line-height:1.2}nav{display:flex;flex-wrap:wrap;gap:1rem}
a{color:#0645ad}a:focus-visible{outline:3px solid #b34b00;outline-offset:4px}
.notice{padding:1rem;border:2px solid #526576;border-radius:.4rem}dt{font-weight:700}dd{margin:0 0 .6rem;overflow-wrap:anywhere}
[data-state="stale"],[data-state="unknown"]{border-color:#8a4b00}time{overflow-wrap:anywhere}
</style></head><body><main>
<h1>AtR search index</h1>
<p>This page reports the last successful index build. Search articles on the blog.</p>
<nav aria-label="Search and update links"><a href="https://www.awakeningtoreality.com/search">Open blog search</a>
<a href="status.json" download="atr-search-status.json">Download build status (JSON)</a>
<a href="https://github.com/sohweiyu2023/atr-search/actions/workflows/build-search.yml">View update history</a></nav>
<h2>Last successful update</h2>
<dl><dt>Built (UTC)</dt><dd><time id="built" datetime="%s">%s</time></dd>
<dt>Articles</dt><dd>%d</dd><dt>Chinese index</dt><dd>%d</dd><dt>Japanese index</dt><dd>%d</dd></dl>
<p id="freshness" class="notice" role="status" aria-live="polite" data-state="unknown">Compare the UTC build time above with the current time. Automatic updates are scheduled every three hours and can be delayed.</p>
<h2>If results look out of date</h2><p>The previous successful index remains available when an update fails. A stale timestamp alone cannot identify the cause. Open update history to check the latest build and deployment outcome.</p>
<p>No build runs from this page. Repository owners can use the existing GitHub workflow controls after checking that a run is not already active.</p>
<noscript><p>Live freshness calculation requires JavaScript; the build time and links above work without it.</p></noscript>
</main><script>
(function(){var node=document.getElementById('freshness'),built=Date.parse(document.getElementById('built').getAttribute('datetime'));
function update(){var age=Date.now()-built,state,text;
if(!Number.isFinite(age)||age< -300000){state='unknown';text='Freshness is unknown. Check the UTC build time and your device clock, then view update history.';}
else if(age>43200000){state='stale';text='This last successful index is over 12 hours old. Scheduled runs may be delayed or failing; view update history for the confirmed outcome.';}
else{state='recent';text='A successful index was built within the last 12 hours. Updates are scheduled every three hours; this does not confirm the outcome of a later run.';}
if(node.dataset.state!==state)node.dataset.state=state;if(node.textContent!==text)node.textContent=text;
}update();setInterval(update,60000);addEventListener('focus',update);document.addEventListener('visibilitychange',function(){if(!document.hidden)update();});
}());
</script></body></html>''' % (built, built, status['posts'], status['chinese'], status['japanese'])
