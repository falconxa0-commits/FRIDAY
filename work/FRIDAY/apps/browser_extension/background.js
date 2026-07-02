chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
  if (request.action === "ask_friday") {
    fetch("http://localhost:8000/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: request.message })
    })
    .then(r => r.json())
    .then(data => sendResponse({ response: data.response }))
    .catch(e => sendResponse({ response: "Friday is unreachable." }));
    return true;
  }
});
