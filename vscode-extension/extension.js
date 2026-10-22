const vscode = require('vscode');
const path = require('path');
const fs = require('fs');
const crypto = require('crypto');
const { execFile } = require('child_process');

function activate(context) {
  context.subscriptions.push(vscode.commands.registerCommand('knitcodeStatic.open', async () => {
    if (!vscode.workspace.isTrusted) return;
    if ((vscode.workspace.workspaceFolders || []).some(f => f.uri.scheme !== 'file')) {
      vscode.window.showErrorMessage('KnitCode Static은 로컬 파일 시스템 프로젝트가 필요합니다.'); return;
    }
    const folders = vscode.workspace.workspaceFolders || [];
    const folder = folders.length === 1 ? folders[0] : await vscode.window.showWorkspaceFolderPick();
    if (!folder) { vscode.window.showInformationMessage('먼저 분석할 프로젝트 폴더를 열어주세요.'); return; }
    const root = folder.uri.fsPath;
    const activeFile = vscode.window.activeTextEditor?.document.uri;
    const initialFile = activeFile?.scheme === 'file' ? path.relative(root, activeFile.fsPath).split(path.sep).join('/') : undefined;
    const media = vscode.Uri.joinPath(context.extensionUri, 'media');
    const panel = vscode.window.createWebviewPanel('knitcodeStatic.map', `KnitCode Static Flow · ${folder.name}`, vscode.ViewColumn.Beside,
      { enableScripts: true, localResourceRoots: [media], retainContextWhenHidden: true });
    const nonce = crypto.randomBytes(16).toString('hex');
    const resource = name => panel.webview.asWebviewUri(vscode.Uri.joinPath(media, name));
    panel.webview.html = `<!doctype html><html lang="ko"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src ${panel.webview.cspSource}; script-src 'nonce-${nonce}';"><link rel="stylesheet" href="${resource('map.css')}"></head><body>
      <header><div><b>KnitCode <em>Flow</em></b><span>함수에서 시작하는 코드 탐색</span></div><button id="refresh">재분석</button></header>
      <p id="status" role="status">분석 준비 중…</p>
      <main><aside class="browser"><input id="search" placeholder="파일·함수 검색" aria-label="파일·함수 검색"><div id="tree"></div></aside>
      <section class="workspace"><div id="focus"></div><nav><div><button id="back">← 이전 선택</button><button id="less">− 단계</button><span id="depth"></span><button id="more">+ 단계</button></div><small>호출자를 왼쪽으로 · 호출 대상을 오른쪽으로</small></nav><div id="flow" aria-label="함수 중심 호출 흐름"></div><section id="evidence"></section></section>
      <aside class="history"><h2>파일의 변경 맥락</h2><p id="history-file"></p><div role="group" aria-label="Git 보기"><button id="cochange-tab">함께 변경</button><button id="history-tab">변경 이력</button></div><div id="history"></div></aside></main><script nonce="${nonce}" src="${resource('map.js')}"></script></body></html>`;
    let child, disposed = false, data, busy = false;
    panel.onDidDispose(() => { disposed = true; if (child) child.kill(); });
    async function analyze() {
      if (busy || disposed) return;
      busy = true;
      panel.webview.postMessage({type: 'status', text: 'Python 코드와 Git 이력 분석 중…'});
      const config = vscode.workspace.getConfiguration('knitcodeStatic', folder.uri);
      const bundled = path.join(context.extensionPath, 'vendor');
      const engine = fs.existsSync(path.join(bundled, 'vscode_export.py')) ? bundled : path.dirname(context.extensionPath);
      const python = config.get('pythonPath') || (process.platform === 'win32' ? 'python' : 'python3');
      child = execFile(python, ['-I', '-B', path.join(engine, 'vscode_export.py'), root, '--days', String(config.get('historyDays')), '--limit', String(config.get('commitLimit'))],
        {cwd: engine, env: {...process.env, PYTHONDONTWRITEBYTECODE: '1'}, maxBuffer: 64 * 1024 * 1024, timeout: 180000},
        (error, stdout, stderr) => {
          busy = false; child = undefined;
          if (disposed) return;
          if (error) { panel.webview.postMessage({type: 'status', text: `분석 실패: ${stderr || error.message} · 설정의 knitcodeStatic.pythonPath를 확인하세요.`}); return; }
          try { data = JSON.parse(stdout); panel.webview.postMessage({type: 'data', data, initialFile}); }
          catch (e) { panel.webview.postMessage({type: 'status', text: `결과를 읽지 못했습니다: ${e.message}`}); }
        });
    }
    async function safeFile(relative) {
      if (typeof relative !== 'string' || path.isAbsolute(relative)) throw new Error('잘못된 파일 경로');
      const target = await fs.promises.realpath(path.resolve(root, relative));
      const base = await fs.promises.realpath(root);
      const rel = path.relative(base, target);
      if (rel.startsWith('..' + path.sep) || rel === '..' || path.isAbsolute(rel)) throw new Error('프로젝트 밖의 파일');
      return vscode.Uri.file(target);
    }
    panel.webview.onDidReceiveMessage(async message => {
      try {
        if (!message || typeof message !== 'object') return;
        if (message.type === 'ready' || message.type === 'refresh') await analyze();
        if (message.type === 'open' && data) {
          const node = data.nodes.find(n => n.id === message.id);
          if (!node) return;
          const line = Number.isInteger(message.line) && (data.edges.some(e => e.source === node.id && e.lineno === message.line) || data.call_statuses.some(c => c.file === node.file && (node.type === 'file' || c.caller_id === node.id) && c.line === message.line)) ? message.line : node.lineno || 1;
          const document = await vscode.workspace.openTextDocument(await safeFile(node.file));
          const position = new vscode.Position(Math.min(Math.max(0, line - 1), document.lineCount - 1), 0);
          await vscode.window.showTextDocument(document, {viewColumn: vscode.ViewColumn.One, selection: new vscode.Range(position, position)});
        }
        if (message.type === 'commit' && data) {
          const commit = data.history.commits.find(c => c.sha === message.sha);
          if (!commit) return;
          execFile('git', ['-C', root, 'show', '--format=fuller', '--stat', '--patch', commit.sha, '--', '.'], {maxBuffer: 16 * 1024 * 1024, timeout: 30000}, async (error, text) => {
            if (error) { vscode.window.showErrorMessage(error.message); return; }
            const doc = await vscode.workspace.openTextDocument({language: 'diff', content: text});
            await vscode.window.showTextDocument(doc, vscode.ViewColumn.One);
          });
        }
      } catch (error) { vscode.window.showErrorMessage(`KnitCode: ${error.message}`); }
    });
  }));
}
module.exports = {activate};
