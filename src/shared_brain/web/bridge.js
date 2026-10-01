const requests = new Map();
let requestId = 0;
window.desktop = new Proxy({}, {
  get: (_, method) => (...args) => new Promise(resolve => {
    const id = ++requestId;
    requests.set(id, resolve);
    window.chrome.webview.postMessage({id, method, args});
  })
});
window.chrome.webview.addEventListener("message", ({data}) => {
  requests.get(data.id)(data.result);
  requests.delete(data.id);
});
document.addEventListener("DOMContentLoaded", () => {
  const send = value => window.chrome.webview.postMessage(value);
  document.querySelector(".titlebar").addEventListener("mousedown", e => {
    if (e.button === 0 && !e.target.closest("button")) send("drag");
  });
  document.querySelector(".titlebar").addEventListener("dblclick", e => {
    if (!e.target.closest("button")) send("maximize");
  });
  document.querySelectorAll("[data-window]").forEach(button => {
    button.onclick = () => send(button.dataset.window);
  });
  window.dispatchEvent(new Event("desktopready"));
});
