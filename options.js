const input = document.getElementById("apiKey");
const status = document.getElementById("status");

chrome.storage.local.get(["typesafeApiKey"], (result) => {
  if (result.typesafeApiKey) {
    input.value = result.typesafeApiKey;
  }
});

document.getElementById("save").addEventListener("click", () => {
  const value = input.value.trim();
  chrome.storage.local.set({ typesafeApiKey: value }, () => {
    status.textContent = value ? "Saved." : "Cleared.";
    setTimeout(() => { status.textContent = ""; }, 2000);
  });
});
