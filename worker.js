export default { async fetch(request) { const url = new URL(request.url); const targetUrl = "https://botapi.rubika.ir" + url.pathname + url.search; return fetch(new Request(targetUrl, request)); } }
