const reportForm = document.getElementById("report-form");

if (reportForm) {
  const status = document.getElementById("report-status");
  const submit = reportForm.querySelector("button[type=submit]");
  const lang = reportForm.dataset.lang;
  let pendingPayload = null;
  reportForm.addEventListener("input", () => { pendingPayload = null; });
  reportForm.addEventListener("change", () => { pendingPayload = null; });
  const copy = {
    en: {
      sending: "Sending this report for private review…",
      failed: "The report was not accepted. Check the fields and try again.",
      limited: "Reporting is temporarily limited. Please try again later.",
      causePair: "Choose both the cause and what you established it from, or neither.",
      badCause: "That cause does not fit this issue type, result or basis. Change it or choose none.",
      consent: "Review the fields and authorize this submission first.",
    },
    ko: {
      sending: "비공개 검토용 제보를 전송 중입니다…",
      failed: "제보가 접수되지 않았습니다. 필드를 확인하고 다시 시도하세요.",
      limited: "제보 접수가 일시 제한됐습니다. 나중에 다시 시도하세요.",
      causePair: "원인과 그 근거를 둘 다 고르거나 둘 다 비워 두세요.",
      badCause: "고른 원인이 문제 유형·결과·근거와 맞지 않습니다. 바꾸거나 비워 두세요.",
      consent: "필드를 확인하고 이번 제출을 허락해 주세요.",
    },
  }[lang];

  reportForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!document.getElementById("report-consent").checked) {
      status.textContent = copy.consent;
      return;
    }
    const values = new FormData(reportForm);
    const source = values.get("attempt_source");
    const cause = values.get("cause");
    const causeBasis = values.get("cause_basis");
    if (!pendingPayload && Boolean(cause) !== Boolean(causeBasis)) {
      status.textContent = copy.causePair;
      return;
    }
    const payload = pendingPayload || {
      schema_version: 1,
      site_host: String(values.get("site_host")).trim().toLowerCase(),
      task_step: values.get("task_step"),
      error_class: values.get("error_class"),
      observed_at: new Date().toISOString(),
      agent_family: values.get("agent_family"),
      attempt_actor: source === "agent_cloud_browser" ? "agent" : "human",
      attempt_env: source,
      outcome: values.get("outcome"),
      challenge_vendor: "unknown",
      challenge_kind: "unknown",
      ...(cause && causeBasis ? { cause, cause_basis: causeBasis } : {}),
      synthetic: false,
      idempotency_key: crypto.randomUUID(),
      consent_confirmed: true,
    };
    pendingPayload = payload;
    submit.disabled = true;
    status.textContent = copy.sending;
    try {
      const response = await fetch("/v1/observations", {
        method: "POST",
        // Marks the report as sent from this form (stored as its received channel, nothing else).
        headers: { "Content-Type": "application/json", "X-AHN-Channel": "web" },
        body: JSON.stringify(payload),
      });
      const result = await response.json();
      if (!response.ok) {
        status.textContent = response.status === 429 ? copy.limited : result.error === "invalid_cause" ? copy.badCause : result.error === "consent_required" ? copy.consent : copy.failed;
        submit.disabled = false;
        return;
      }
      document.getElementById("receipt-id").value = result.observation_id;
      document.getElementById("withdrawal-token").value = result.withdrawal_token;
      document.getElementById("withdraw-id").value = result.observation_id;
      document.getElementById("withdraw-token").value = result.withdrawal_token;
      document.getElementById("report-receipt").hidden = false;
      reportForm.hidden = true;
    } catch {
      status.textContent = copy.failed;
      submit.disabled = false;
    }
  });
}

const withdrawForm = document.getElementById("withdraw-form");
if (withdrawForm) {
  const status = document.getElementById("withdraw-status");
  const lang = withdrawForm.dataset.lang;
  withdrawForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const id = document.getElementById("withdraw-id").value.trim();
    const token = document.getElementById("withdraw-token").value.trim();
    if (!/^[0-9a-f-]{36}$/i.test(id) || !/^[0-9a-f]{64}$/i.test(token)) {
      status.textContent = lang === "en" ? "Check the ID and token." : "ID와 토큰을 확인하세요.";
      return;
    }
    try {
      const response = await fetch(`/v1/observations/${encodeURIComponent(id)}`, {
        method: "DELETE",
        headers: { "X-Withdrawal-Token": token },
      });
      status.textContent = response.ok
        ? (lang === "en" ? "Report withdrawn." : "제보를 철회했습니다.")
        : (lang === "en" ? "No matching report or token." : "일치하는 제보나 토큰이 없습니다.");
    } catch {
      status.textContent = lang === "en" ? "Could not reach the service." : "서비스에 접속하지 못했습니다.";
    }
  });
}
