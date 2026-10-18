const copyButton = document.getElementById("copy-agent-prompt");
const promptField = document.getElementById("agent-start-prompt");
const copyStatus = document.getElementById("prompt-copy-status");

if (copyButton && promptField && copyStatus) {
  copyButton.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(promptField.value);
      copyStatus.textContent = copyButton.dataset.copiedLabel;
    } catch {
      promptField.focus();
      promptField.select();
      copyStatus.textContent = copyButton.dataset.failedLabel;
    }
  });
}
