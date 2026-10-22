/* A small DOM harness for behavior checks; it does not validate browser layout. */
class MockElement {
  constructor(tag) { this.tag=tag; this.children=[]; this.events={}; this.attrs={}; this.dataset={}; this.value=''; this.textContent=''; }
  append(node) { this.children.push(node); }
  replaceChildren(...nodes) { this.children=nodes; this.textContent=''; }
  addEventListener(type, callback) { this.events[type]=callback; }
  setAttribute(key,value) { this.attrs[key]=value; }
}
const elements=new Map(), listeners={}, messages=[];
const document={
  getElementById(id) { if(!elements.has(id)) elements.set(id,new MockElement('div')); return elements.get(id); },
  createElement(tag) { return new MockElement(tag); },
  querySelector() { return null; }
};
const window={addEventListener(type,callback){listeners[type]=callback;}};
function requestAnimationFrame() {} // Layout is verified separately in a real browser.
function acquireVsCodeApi() { return {postMessage(message){messages.push(message);}}; }
function assert(value,message) { if(!value)throw new Error(message); }
function text(node) { return node.textContent+' '+node.children.map(text).join(' '); }
function find(node,predicate) { if(predicate(node))return node; for(const child of node.children){const match=find(child,predicate);if(match)return match;} }
load('vscode-extension/media/map.js');
const payload=JSON.parse(readFile(arguments[0]));
const target=payload.nodes.find(n=>n.name.endsWith('.target') || n.name==='target');
const caller=payload.nodes.find(n=>n.name.endsWith('.caller') || n.name==='caller');
listeners.message({data:{type:'data',data:payload,initialFile:target.file}});
choose(target.id);
assert(layers(target.id,true,1)[0].includes(caller.id),'directed caller expansion');
assert(text(elements.get('flow')).includes('코드 ↗'),'source actions');
elements.get('more').events.click();
assert(text(elements.get('depth')).includes('2단계'),'depth control');
choose(caller.id);
assert(text(elements.get('evidence')).includes('미확정'),'unknown call visible');
assert(text(elements.get('evidence')).includes('조건부 콜백 후보'),'callbacks distinguished');
assert(!text(elements.get('status')).includes('반복 한도 도달'),'no fabricated convergence warning');
const link=find(elements.get('evidence'),n=>n.tag==='button'&&n.textContent.includes('unknown'));
link.events.click();
assert(messages.at(-1).type==='open'&&messages.at(-1).line===5,'unknown callsite source action');
elements.get('back').events.click();
assert(text(elements.get('focus')).includes('target'),'back restores focus');
calls.push({source:target.id,target:target.id,type:'calls'});
assert(!layers(target.id,true,3).flat().includes(target.id),'cycle terminates');
elements.get('search').value='<script>';elements.get('search').events.input();
assert(!elements.get('tree').children.length,'search filters tree');
listeners.message({data:{type:'data',data:{...payload,nodes:[],edges:[]}}});
assert(text(elements.get('flow')).includes('분석할 Python 코드가 없습니다'),'empty clears view');
print('Flow webview behavior OK');
