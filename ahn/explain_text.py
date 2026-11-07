"""D108: the fixed sentences of GET /v1/why, English and Korean, and the fixed phrases that fill their blanks.

Every sentence is a template whose blanks are filled only from AHN's own data, the curated platform table or a fixed phrase
table below. No site text, no caller text and no database string is ever put into a sentence. ``tests/test_explain_templates.py``
checks that every template exists in both languages, that its blanks are in ``BLANKS``, that its words are not among the
forbidden ones, and that the longest filled text fits its limit.
"""

from __future__ import annotations

from typing import Any

# Blanks a template may use. A blank with no value means the sentence is left out; it is never rendered empty.
BLANKS = frozenset({
    "host", "date", "date2", "time", "time2", "platform", "platform_cap", "platform2", "vendor", "url", "quote", "quote2",
    "provider", "indicator", "token_scope", "state", "auth", "mechanism", "layer_words", "reason_words", "cause_line",
    "outcome_words", "vendor_part", "field_words", "from_words", "to_words", "n", "step_words", "env_words", "stop_words",
    "since", "person_line", "reason_short", "admin_mark", "unread_reason", "token_words",
})

TEMPLATES: dict[str, dict[str, str]] = {}


def _t(template_id: str, en: str, ko: str) -> None:
    TEMPLATES[template_id] = {"en": en, "ko": ko}


# --- L1 site ----------------------------------------------------------------------------------------------------------
_t("S-L1A-ALLOW",
   "The robots.txt AHN read for {host} on {date} does not disallow the site root for {token_scope}. It states crawler rules only and does not show that your step will work.",
   "AHN 이 {date} 에 읽은 {host} 의 robots.txt 는 {token_scope} 에 대해 사이트 루트를 막지 않아요. 크롤러 규칙의 선언일 뿐, 당신의 단계가 될 거라는 뜻은 아니에요.")
_t("S-L1A-ABSENT",
   "When AHN read {host} on {date}, the site had no robots.txt, so it declares no crawler rules there. That does not show that your step will work.",
   "AHN 이 {date} 에 읽었을 때 {host} 에는 robots.txt 가 없었어요. 그곳에 선언된 크롤러 규칙이 없다는 뜻일 뿐, 당신의 단계가 될 거라는 뜻은 아니에요.")
_t("S-L1A-BLOCK",
   "The robots.txt AHN read for {host} on {date} disallows the site root for {token_scope}. This is the site's declaration; AHN cannot confirm it is what stopped your step.",
   "AHN 이 {date} 에 읽은 {host} 의 robots.txt 는 {token_scope} 에 대해 사이트 루트를 막아요. 사이트의 선언이며, 이것이 당신의 단계를 멈춘 원인인지는 AHN 이 확인하지 못해요.")
_t("S-L1A-SOME",
   "The robots.txt AHN read for {host} on {date} blocks some agent tokens but not all agents (*).",
   "AHN 이 {date} 에 읽은 {host} 의 robots.txt 는 일부 에이전트 토큰을 막지만 모든 에이전트(*)를 막지는 않아요.")
_t("S-L1A-BACKGROUND-BROWSER",
   "The same step works in your usual browser, so AHN treats this site's robots.txt as background here, not as the deciding layer.",
   "같은 단계가 보통 브라우저에서는 되므로, AHN 은 이 사이트의 robots.txt 를 결정 층이 아니라 참고로 둬요.")
_t("S-L1A-BACKGROUND-ACCOUNT",
   "robots.txt states crawler rules; it does not say whether a signed-in person's agent can finish a task in their own account. AHN treats it as background here.",
   "robots.txt 는 크롤러 규칙이에요. 로그인한 사람의 에이전트가 자기 계정의 작업을 끝낼 수 있는지는 말해 주지 않아요. 그래서 여기서는 참고로 둬요.")
_t("S-L1A-UNREAD",
   "AHN has no usable read of this site's robots.txt ({state}). Its declared policy is unknown, not permissive.",
   "AHN 은 이 사이트의 robots.txt 를 쓸 수 있게 읽지 못했어요({state}). 선언 정책은 \"허용\"이 아니라 \"모름\"이에요.")
_t("S-L1A-STALE",
   "This read is from {date} and may be out of date.",
   "이 읽기는 {date} 기준이라 지금과 다를 수 있어요.")
_t("S-L1A-TOKEN-UNDOC",
   "{vendor}'s official documentation does not say which robots.txt token, if any, {platform} sends, so AHN cannot say which rule group applies to it.",
   "{vendor} 공식 문서에는 {platform} 이 어떤 robots.txt 토큰을 보내는지(보내기는 하는지) 적혀 있지 않아서, 어느 규칙 묶음이 적용되는지 AHN 이 말할 수 없어요.")
_t("S-L1B-NOSEE",
   "AHN does not observe bot-defense systems on this site, so it cannot say whether one is in use or recently changed.",
   "AHN 은 이 사이트의 봇 방어 체계를 관측하지 않아요. 쓰는지, 최근 바뀌었는지 말할 수 없어요.")
_t("S-L1B-AHN-HOME",
   "When AHN's server requested {host}'s home page on {date}, it received {outcome_words}{vendor_part}. Your tool may get a different answer.",
   "AHN 서버가 {date} 에 {host} 의 홈 페이지를 요청했을 때 받은 것은 {outcome_words}{vendor_part} 였어요. 당신의 도구는 다른 답을 받을 수 있어요.")
_t("S-L1C-API-AUTH",
   "The site's official developer documentation ({url}) describes {auth} sign-in for its API.",
   "사이트 공식 개발자 문서({url})는 API 인증 방식을 {auth} 로 적어요.")
_t("S-L1D-ACTIVE",
   "{provider}'s status page has shown {indicator} since {time} (AHN read it at {time2}).",
   "{provider} 상태 페이지가 {time} 부터 {indicator} 를 표시하고 있어요(AHN 읽기 {time2}).")
_t("S-L1D-ACTIVE-NOTIME",
   "{provider}'s status page shows {indicator} (AHN read it at {time2}); it gives no start time AHN can use.",
   "{provider} 상태 페이지가 {indicator} 를 표시하고 있어요(AHN 읽기 {time2}). 쓸 수 있는 시작 시각은 적혀 있지 않아요.")
_t("S-L1D-CLEAR",
   "{provider}'s status page showed no announced incident when AHN read it at {time}. That does not confirm the site works.",
   "AHN 이 {time} 에 읽은 {provider} 상태 페이지에는 발표된 장애가 없었어요. 사이트가 정상이라는 확인은 아니에요.")
_t("S-L1D-NONE",
   "AHN has no connected status feed for this site.",
   "AHN 에는 이 사이트의 상태 피드가 연결돼 있지 않아요.")
_t("S-L1E-CHANGED",
   "On {date}, AHN recorded a change in {host}'s {field_words}: {from_words} to {to_words}.",
   "{date} 에 AHN 이 {host} 의 {field_words} 변화를 기록했어요: {from_words} 에서 {to_words} 로.")
_t("S-L1E-UNCHANGED",
   "AHN recorded no change in {host}'s declared files or home-page check from {date} to {date2}. It reads them about once a day to once a week and sees nothing between reads.",
   "{date}~{date2} 에 AHN 이 기록한 {host} 의 선언 파일·홈 페이지 확인에는 변화가 없었어요. 하루에서 일주일에 한 번쯤 읽고, 읽기 사이의 일은 보지 못해요.")
_t("S-L1-CAUSE",
   "AHN's stored measurements for {host} say: {cause_line}",
   "AHN 이 저장한 {host} 의 측정은 이렇게 말해요: {cause_line}")

# --- L2 platform ------------------------------------------------------------------------------------------------------
_t("S-L2-DOC",
   "{platform}'s official documentation ({url}, checked {date}){admin_mark} says: \"{quote}\"",
   "{platform} 공식 문서({url}, {date} 확인){admin_mark}는 이렇게 적어요: \"{quote}\"")
_t("S-L2-SITE-SIDE",
   "{platform}'s official documentation ({url}, checked {date}) says some limits are set by websites: \"{quote}\"",
   "{platform} 공식 문서({url}, {date} 확인)는 일부 제한을 웹사이트가 정한다고 적어요: \"{quote}\"")
_t("S-L2-PROHIBITED",
   "{platform}'s documentation ({url}) lists actions it does not take, including: \"{quote}\"",
   "{platform} 문서({url})는 하지 않는 행동 목록에 이런 문구를 적어요: \"{quote}\"")
_t("S-L2-NOCLASS",
   "AHN does not judge whether this site falls under that description.",
   "이 사이트가 그 설명에 드는지는 AHN 이 판단하지 않아요.")
_t("S-L2-CONFLICT",
   "{platform}'s official pages describe this differently (\"{quote}\" / \"{quote2}\"). AHN does not choose between them.",
   "{platform} 공식 문서끼리 이 부분을 다르게 적어요(\"{quote}\" / \"{quote2}\"). AHN 은 어느 쪽인지 고르지 않아요.")
_t("S-L2-NODOC",
   "AHN found no official documentation from {vendor} on which sites {platform} limits or how it handles sign-in (checked {date}). AHN cannot say whether a {platform} rule is involved.",
   "{vendor} 가 {platform} 이 어떤 사이트를 제한하는지·로그인을 어떻게 다루는지 공식 문서를 내지 않았어요({date} 확인). {platform} 규칙이 관련됐는지 AHN 은 말할 수 없어요.")
_t("S-L2-UNREAD",
   "{vendor} publishes a help page on this ({url}), but AHN could not read it (checked {date}): {unread_reason}.",
   "{vendor} 가 관련 도움말({url})을 내고 있지만 AHN 은 읽지 못했어요({date} 확인): {unread_reason}.")
_t("S-L2-NOT-REVIEWED",
   "AHN has not reviewed {platform}'s documentation yet.",
   "AHN 은 아직 {platform} 문서를 검토하지 않았어요.")
_t("S-L2-NOLIST",
   "{platform} does not publish a full list of the sites it limits, and AHN does not try to rebuild one.",
   "{platform} 은 제한하는 사이트의 전체 목록을 공개하지 않고, AHN 은 그것을 복원하려 하지 않아요.")
_t("S-L2-MSG-MATCH",
   "You chose the message {platform}'s documentation shows when {mechanism}. If your screen shows it, that documentation is the place to look next.",
   "고른 메시지는 {platform} 문서가 \"{mechanism}\" 때 뜬다고 적은 문구예요. 화면에 그 문구가 보이면 다음으로 볼 곳은 그 문서예요.")
_t("S-L2-MSG-OTHER-PLATFORM",
   "That message is documented for {platform2}, not for {platform}. AHN does not use it for {platform}.",
   "그 메시지는 {platform} 이 아니라 {platform2} 문서에 있는 문구예요. {platform} 판단에는 쓰지 않아요.")

# --- L3 account and agent environment ---------------------------------------------------------------------------------
_t("S-L3-SCOPE",
   "AHN cannot see your account, device, network or company settings, and makes no finding about them.",
   "AHN 은 당신의 계정·기기·네트워크·회사 설정을 볼 수 없고, 그에 대해 어떤 판정도 하지 않아요.")
_t("S-L3-SSO-GENERAL",
   "Some company sign-in systems allow access only from managed browsers or devices. Only your IT team can say whether yours does.",
   "회사 로그인 체계 중에는 관리되는 브라우저·기기에서만 접속을 허용하는 것이 있어요. 당신 회사가 그런지는 IT 팀만 알 수 있어요.")
_t("S-L3-C1",
   "Is the person signed in to {host} inside {platform}'s own browser, not only in their usual browser?",
   "{platform} 자체 브라우저 안에서 {host} 에 로그인돼 있나요(보통 브라우저만이 아니라)?")
_t("S-L3-C2",
   "Does signing in now ask for an extra approval on another device?",
   "지금 로그인할 때 다른 기기에서 추가 승인을 요구하나요?")
_t("S-L3A-VERIFY",
   "You said the step stopped at an extra verification after the password (a code, an app approval or a new-device check). That step is meant for the account holder; AHN cannot see whether the site sent the request or where.",
   "비밀번호 뒤 추가 인증(코드·앱 승인·새 기기 확인)에서 멈췄다고 했어요. 그 단계는 계정 주인을 위한 것이고, 사이트가 요청을 보냈는지·어디로 보냈는지 AHN 은 볼 수 없어요.")
_t("S-L3A-SIGNIN",
   "You said the step stopped at the site's own sign-in. Signing in is the account holder's step; AHN cannot see the account and makes no finding about it.",
   "사이트 자체 로그인에서 멈췄다고 했어요. 로그인은 계정 주인의 단계이고, AHN 은 계정을 볼 수 없어서 그에 대한 판정은 하지 않아요.")
_t("S-L3A-AFTER",
   "You said an action after signing in failed. AHN cannot see the account or what the site did, and makes no finding about them.",
   "로그인 뒤의 행동이 실패했다고 했어요. AHN 은 계정이나 사이트가 한 일을 볼 수 없어서 그에 대한 판정은 하지 않아요.")
_t("S-L3B-CLOUD",
   "You said the agent works in a cloud browser, not on the person's device, so the site sees a sign-in that did not start on that device. AHN cannot see how this site treats that.",
   "에이전트가 사람 기기가 아니라 클라우드 브라우저에서 일한다고 했어요. 그래서 사이트는 그 기기에서 시작되지 않은 로그인을 보게 돼요. 이 사이트가 그것을 어떻게 다루는지 AHN 은 볼 수 없어요.")
_t("S-L3B-DEVICE",
   "{platform}'s documentation ({url}) says: \"{quote}\"",
   "{platform} 문서({url})는 이렇게 적어요: \"{quote}\"")

# --- L4 task ----------------------------------------------------------------------------------------------------------
_t("S-L4-TERMS",
   "The site's terms page ({url}) contains this sentence: \"{quote}\" AHN does not interpret it.",
   "사이트 약관 페이지({url})에 이 문장이 있어요: \"{quote}\" AHN 은 이를 해석하지 않아요.")
_t("S-L4-PLATFORM-CONFIRM",
   "{platform}'s documentation says it asks the person before some actions: \"{quote}\"",
   "{platform} 문서는 일부 행동 전에 사람에게 묻는다고 적어요: \"{quote}\"")
_t("S-L4-OFFICIAL-PATH-ACCOUNT",
   "The listed official developer path may be meant for developers or partners and may not do tasks in your own account.",
   "등록된 공식 개발자 경로는 개발자·파트너용일 수 있어, 당신 계정의 작업을 대신하지 못할 수 있어요.")
_t("S-L4-NOJUDGE",
   "AHN does not decide whether a task is allowed.",
   "작업이 허용되는지는 AHN 이 판단하지 않아요.")

# --- common, others, where to look, person ----------------------------------------------------------------------------
_t("S-G-BASIS",
   "This answer uses only declarations, official documents and AHN's own read records. It did not test the site, the agent or your account.",
   "이 답은 선언·공식 문서·AHN 의 읽기 기록만으로 만들었어요. 사이트·에이전트·당신 계정을 실제로 시험하지 않았어요.")
_t("S-G-NO-SUCCESS",
   "AHN is consulted only when something fails, so it cannot tell whether other agents succeed on this site.",
   "AHN 은 실패할 때만 호출돼서, 다른 에이전트가 이 사이트에서 되는지는 알 수 없어요.")
_t("S-G-WINDOW-EMPTY",
   "AHN saw no change in this period. It does not see bot defenses, accounts, or platforms' unpublished lists.",
   "이 기간에 AHN 이 본 변화는 없어요. 봇 방어·계정·플랫폼의 비공개 목록은 보지 못해요.")
_t("S-G-WINDOW-LIMITS",
   "AHN reads a host's files about once a day to once a week and sees nothing between reads. It does not see bot defenses, accounts, or platforms' unpublished lists.",
   "AHN 은 호스트의 파일을 하루에서 일주일에 한 번쯤 읽고, 읽기 사이의 일은 보지 못해요. 봇 방어·계정·플랫폼의 비공개 목록도 보지 못해요.")
_t("S-G-APEX",
   "You entered an address with more than a registered domain; AHN looked up {host} only and did not send the rest.",
   "등록 도메인보다 긴 주소를 넣었어요. AHN 은 등록 도메인 {host} 만 조회했고 나머지는 보내지 않았어요.")
_t("S-G-PATH-DROPPED",
   "AHN kept only the hostname; the path and query were not sent.",
   "AHN 은 호스트명만 남겼고 경로·쿼리는 보내지 않았어요.")
_t("S-O-NONE",
   "No other agent reported a problem on {host} in the last 7 days. That does not show the site works for them.",
   "지난 7일 동안 다른 에이전트가 {host} 의 문제를 알린 적은 없어요. 그렇다고 다른 에이전트에게 된다는 뜻은 아니에요.")
_t("S-O-SOME",
   "{n} report(s) from other agents in the last 7 days, not verified by AHN:",
   "지난 7일 동안 다른 에이전트가 올린 제보 {n}건이에요. AHN 이 검증한 것은 아니에요:")
_t("S-O-ROW",
   "{date}: {outcome_words} at {step_words}{env_words}",
   "{date}: {step_words}에서 {outcome_words}{env_words}")
_t("S-W-FIRST",
   "Look first at the {layer_words} layer: {reason_words}. AHN has not confirmed this is the cause.",
   "먼저 {layer_words} 쪽을 보세요: {reason_words}. 이것이 원인이라고 AHN 이 확인한 것은 아니에요.")
_t("S-W-UNKNOWN",
   "AHN has no evidence that points to one layer yet. The checks below split the layers.",
   "아직 한 층을 가리키는 근거가 없어요. 아래 확인이 층을 갈라 줘요.")
_t("S-P-NOT-INDICATED",
   "Nothing in your answers or AHN's records says a person must take this step. This is not a check of the site.",
   "고른 답과 AHN 기록 어디에도 사람이 이 단계를 해야 한다는 표시는 없어요. 사이트를 확인한 결과는 아니에요.")
_t("S-P-VERIFY-DEVICE",
   "The site asks for an approval or code on the account holder's own device. Only that person can complete it.",
   "사이트가 계정 주인의 기기로 승인이나 코드를 요구해요. 그 사람만 끝낼 수 있어요.")
_t("S-P-SIGN-IN",
   "Signing in is the account holder's step. An agent does not receive, store or pass on credentials.",
   "로그인은 계정 주인의 단계예요. 에이전트는 자격 증명을 받거나 저장하거나 넘기지 않아요.")
_t("S-P-APPROVAL",
   "The product asks the person to approve each action here. Approving is the person's own decision.",
   "이 제품은 여기서 행동마다 사람에게 승인을 물어요. 승인 여부는 사람이 정해요.")
_t("S-P-COMPANY-IT",
   "Whether a company sign-in accepts this browser or device is decided by the company's IT team.",
   "회사 로그인이 이 브라우저·기기를 받아들이는지는 회사 IT 팀이 정해요.")
_t("S-P-PLATFORM-ADMIN",
   "The message you chose says an organization policy applies. The organization's administrator decides that.",
   "고른 메시지는 조직 정책이 적용된다는 뜻이에요. 그건 조직 관리자가 정해요.")
_t("S-P-PLATFORM-REFUSED",
   "{platform}'s official documentation says it does not open some kinds of sites or asks first. AHN does not judge whether this site is one of them. When the agent stops here, the person decides this step.",
   "{platform} 공식 문서는 이런 사이트·행동을 하지 않거나 먼저 물어본다고 적어요. 이 사이트가 거기 드는지는 판단하지 않아요. 에이전트가 멈췄으면 그 단계는 사람이 정해요.")
_t("S-P-SERVER-HAND-OFF",
   "AHN's next action for this lookup also hands this step to the person.",
   "서버의 다음 행동도 이 단계를 사람에게 넘기라고 해요.")

# --- things to check (CHK) and what to tell a person (H) --------------------------------------------------------------
_t("CHK-BROWSER-COMPARE",
   "A person can try the same step once in their usual browser, signed in to the same account. The result tells the site layer apart from the agent and account layers. It is a check, not a way for the agent to continue.",
   "사람이 같은 계정으로 보통 브라우저에서 같은 단계를 한 번 해 보세요. 결과가 사이트 층과 에이전트·계정 층을 갈라 줘요. 확인용이지 에이전트가 계속하는 방법이 아니에요.")
_t("CHK-MSG-MATCH",
   "Compare the message on screen with the messages {platform}'s documentation lists ({url}).",
   "화면 메시지를 {platform} 문서({url})가 적은 메시지와 비교해 보세요.")
_t("CHK-ORG-ADMIN",
   "If {platform} comes through an organization plan, ask its administrator whether a site rule covers {host} ({url}).",
   "{platform} 을 조직 플랜으로 쓰면 관리자에게 {host} 에 사이트 규칙이 걸려 있는지 물어보세요({url}).")
_t("CHK-SAFETY-CONFIRM",
   "If {platform} asks for approval at each action, its documentation describes that step ({url}). Approving is the person's own decision.",
   "{platform} 이 행동마다 승인을 묻는다면 그 절차는 문서({url})에 있어요. 승인 여부는 사람이 정해요.")
_t("CHK-IT-SSO",
   "Ask your IT team whether this app's company sign-in is allowed from {platform}'s separate browser profile. Use the handoff text below.",
   "회사 IT 에 이 앱의 회사 로그인이 {platform} 의 별도 브라우저 프로필에서 허용되는지 물어보세요. 아래 인계문을 쓰세요.")
_t("CHK-PERSON-VERIFY",
   "The account holder completes this verification on their own device. If no request arrives while the agent is signing in, the account holder can sign in themselves in their own browser or the site's app and do the step there.",
   "계정 주인이 자기 기기에서 이 인증을 끝내요. 에이전트가 로그인하는 동안 요청이 오지 않으면, 계정 주인이 자기 브라우저나 사이트 앱에서 직접 로그인해 그 단계를 하면 돼요.")
_t("CHK-SIGNED-IN-TAIL", "", "")  # placeholder removed below; CHK-SIGNED-IN is built from S-L3-C1 and S-L3-C2
del TEMPLATES["CHK-SIGNED-IN-TAIL"]
_t("CHK-PLATFORM-CHANGES",
   "AHN saw {platform}'s page {url} change on {date}. Check the app version and that page.",
   "AHN 이 {date} 에 {platform} 의 페이지 {url} 가 바뀐 것을 봤어요. 앱 버전과 그 페이지를 확인해 보세요.")
_t("CHK-PLATFORM-REMEDY",
   "{platform}'s documentation suggests: \"{quote}\" ({url})",
   "{platform} 문서가 제안하는 것: \"{quote}\" ({url})")
_t("CHK-STATUS",
   "See {provider}'s status page ({url}).",
   "{provider} 상태 페이지({url})를 보세요.")
_t("CHK-SERVER-ACTION",
   "Follow the server's next action as given (server.next_action).",
   "서버의 다음 행동(server.next_action)을 그대로 따르세요.")
_t("CHK-CUSTOMER-PATH",
   "The site's own customer page is {url}.",
   "사이트 자체 고객 페이지는 {url} 이에요.")
_t("CHK-OFFICIAL-PATH",
   "The official developer path is {url}.",
   "공식 개발자 경로는 {url} 이에요.")
_t("CHK-WAIT",
   "Wait instead of retrying; the server gives when to look up once more (server.next_action.retry_after_seconds).",
   "재시도 말고 기다리세요. 다시 조회할 때는 서버가 정해 줘요.")
_t("CHK-SITE-HELP",
   "If the step fails in every browser, the site's own help channel is the next contact.",
   "모든 브라우저에서 안 되면 다음 연락처는 사이트 자체 고객 지원이에요.")

_t("H-IT",
   "Since {since}, signing in to {host} through {platform}'s built-in browser stops at: {stop_words}. The same account works in my usual browser. Is this app's company sign-in allowed from a separate browser profile under our access policy?",
   "{since} 부터 {platform} 내장 브라우저로 {host} 에 로그인하면 \"{stop_words}\" 에서 멈춰요. 같은 계정이 보통 브라우저에서는 돼요. 우리 접근 정책상 별도 브라우저 프로필에서 이 앱의 회사 로그인이 허용되나요?")
_t("H-ORG-ADMIN",
   "Since {since}, {platform} stops on {host} at: {stop_words}. Does our organization's {platform} site policy cover {host}?",
   "{since} 부터 {platform} 이 {host} 에서 \"{stop_words}\" 로 멈춰요. 우리 조직의 {platform} 사이트 정책이 {host} 에 걸려 있나요?")
_t("H-PLATFORM",
   "Since {since}, {platform} stops on {host} at: {stop_words}, while the same step works in a usual browser. App version: (fill in).",
   "{since} 부터 {platform} 이 {host} 에서 \"{stop_words}\" 로 멈추고, 같은 단계는 보통 브라우저에서 돼요. 앱 버전: (직접 적기).")
_t("H-SITE",
   "Since {since}, a step on {host} stops at: {stop_words}, in every browser I tried.",
   "{since} 부터 {host} 의 단계가 제가 써 본 모든 브라우저에서 \"{stop_words}\" 로 멈춰요.")
_t("H-USER",
   "{platform_cap} could not finish this step on {host} (stopped at: {stop_words}). {person_line}",
   "{platform_cap} 이 {host} 의 이 단계를 끝내지 못했어요(\"{stop_words}\"). {person_line}")
_t("H-USER-NOHOST",
   "{platform_cap} could not finish this step (stopped at: {stop_words}). {person_line}",
   "{platform_cap} 이 이 단계를 끝내지 못했어요(\"{stop_words}\"). {person_line}")
_t("H-USER-YES",
   "You need to do this step yourself: {reason_short}.",
   "이 단계는 직접 하셔야 해요: {reason_short}.")
_t("H-USER-NO",
   "AHN found no cause it can confirm; see the checks listed.",
   "AHN 이 확인한 범위에서는 짚이는 곳이 없었어요. 확인 목록을 따라가 보세요.")
_t("TL-PLATFORM-DOC",
   "On {date}, AHN saw {platform}'s page {url} change.",
   "{date} 에 AHN 이 {platform} 의 페이지 {url} 가 바뀐 것을 봤어요.")

# --- fixed phrases that fill blanks (the only values a sentence takes besides AHN's own data) -------------------------
TOKEN_SCOPE = {"en": "all agents (*)", "ko": "모든 에이전트(*)"}
DEFAULT_PLATFORM = {"en": ("the agent", "The agent"), "ko": ("에이전트", "에이전트")}
ADMIN_MARK = {"en": " [admin deployment documentation]", "ko": " [관리 배포 문서]"}
VENDORS = {"anthropic": "Anthropic", "openai": "OpenAI", "google": "Google", "meta": "Meta", "perplexity": "Perplexity"}

ROBOTS_STATE = {
    "pending": {"en": "not read yet", "ko": "아직 읽지 않음"},
    "refused": {"en": "the site refused the read", "ko": "사이트가 읽기를 거절함"},
    "error": {"en": "the read failed", "ko": "읽기 실패"},
    "invalid": {"en": "the file could not be parsed", "ko": "파일을 해석하지 못함"},
    "too_large": {"en": "the file is too large", "ko": "파일이 너무 큼"},
    "not_public": {"en": "the address is not public", "ko": "공개 주소가 아님"},
    "redirected": {"en": "the request was redirected", "ko": "다른 곳으로 리디렉션됨"},
    "none": {"en": "not read for this host", "ko": "이 호스트는 읽지 않음"},
}
UNREAD_REASON = {
    "http_403": {"en": "the page refused AHN's request", "ko": "그 페이지가 AHN 의 요청을 거절했어요"},
    "challenge_page": {"en": "the page showed a human-verification check", "ko": "그 페이지가 사람 확인 화면을 보여 줬어요"},
    "script_rendered": {"en": "the page needs a script to show its text", "ko": "그 페이지는 스크립트가 있어야 글이 보여요"},
    "not_read_this_round": {"en": "not read in the last 90 days", "ko": "최근 90일 안에 읽지 않았어요"},
}
INDICATOR = {
    "minor": {"en": "a minor incident", "ko": "경미한 장애"},
    "major": {"en": "a major incident", "ko": "큰 장애"},
    "critical": {"en": "a critical incident", "ko": "심각한 장애"},
    "maintenance": {"en": "maintenance", "ko": "점검"},
}
HOME_OUTCOME = {
    "reachable": {"en": "a normal page", "ko": "정상 페이지"},
    "redirected_same_host": {"en": "a redirect within the site", "ko": "사이트 안의 리디렉션"},
    "bot_challenge": {"en": "a human-verification page", "ko": "사람 확인 화면"},
    "refused": {"en": "a refusal", "ko": "거절 응답"},
    "rate_limited": {"en": "a rate-limit response", "ko": "요청 제한 응답"},
    "login_wall": {"en": "a sign-in page", "ko": "로그인 화면"},
    "redirected_offsite": {"en": "a redirect to another site", "ko": "다른 사이트로의 리디렉션"},
    "server_error": {"en": "a server error", "ko": "서버 오류"},
    "unreachable": {"en": "no response", "ko": "응답 없음"},
    "other_status": {"en": "an unusual status", "ko": "특이한 상태 코드"},
}
BOT_VENDOR = {"cloudflare": "Cloudflare", "datadome": "DataDome", "perimeterx": "PerimeterX", "aws_waf": "AWS WAF",
              "vercel": "Vercel", "generic_captcha": "CAPTCHA"}
VENDOR_PART = {"en": " with a {vendor} bot-defense marker", "ko": " ({vendor} 봇 방어 표지 포함)"}
AUTH_WORDS = {"none": {"en": "no", "ko": "인증 없음"}, "api_key": {"en": "API key", "ko": "API 키"},
              "oauth1": {"en": "OAuth 1.0", "ko": "OAuth 1.0"}, "oauth2": {"en": "OAuth 2.0", "ko": "OAuth 2.0"},
              "device_code": {"en": "device-code", "ko": "기기 코드"}, "pat": {"en": "personal access token", "ko": "개인 액세스 토큰"}}
MECHANISM = {
    "banner_text": {"en": "an organization policy blocks the site", "ko": "조직 정책이 사이트를 막는다"},
    "confirm_required": {"en": "the product asks a person to approve", "ko": "제품이 사람에게 승인을 묻는다"},
    "blocked_category": {"en": "a site is in a default-blocked category", "ko": "사이트가 기본 차단 범주에 있다"},
    "admin_policy": {"en": "an administrator limits sites", "ko": "관리자가 사이트를 제한한다"},
}
STOP_WORDS = {
    "before_visit": {"en": "the agent refused before opening the site", "ko": "사이트를 열기 전에 에이전트가 거절한 곳"},
    "page_blank_or_blocked": {"en": "a blank, blocked or access-denied page", "ko": "빈 화면이거나 막힌, 접근이 거부된 페이지"},
    "human_verification": {"en": "a human-verification check", "ko": "사람 확인 단계"},
    "company_login": {"en": "the company sign-in page", "ko": "회사 로그인 페이지"},
    "site_login": {"en": "the site's own sign-in", "ko": "사이트 자체 로그인"},
    "after_login_action_fails": {"en": "an action after signing in", "ko": "로그인 뒤의 동작"},
    "tools_off_page_visible": {"en": "the page shows but the agent cannot act on it", "ko": "페이지는 보이는데 에이전트가 동작하지 못하는 곳"},
    "timeout_or_5xx": {"en": "a timeout or server error", "ko": "시간 초과나 서버 오류"},
    "not_sure": {"en": "an unclear step", "ko": "분명하지 않은 단계"},
    "extra_verification": {"en": "an extra verification after the password", "ko": "비밀번호 뒤 추가 인증"},
}
SINCE_WORDS = {"today": {"en": "today", "ko": "오늘"}, "recent": {"en": "recently", "ko": "최근"}}
STEP_WORDS = {
    "site_open": {"en": "opening the site", "ko": "사이트 열기"},
    "login": {"en": "sign-in", "ko": "로그인"},
    "human_verification": {"en": "a human-verification check", "ko": "사람 확인"},
    "service_use": {"en": "using the service", "ko": "서비스 이용"},
    "other": {"en": "a step", "ko": "어떤 단계"},
}
OUTCOME_WORDS = {
    "passed": {"en": "a pass", "ko": "통과"}, "blocked": {"en": "a block", "ko": "막힘"},
    "error": {"en": "an error", "ko": "오류"}, "abandoned": {"en": "a stop without a result", "ko": "결과 없이 중단"},
}
ENV_WORDS = {
    "agent_cloud_browser": {"en": "agent cloud browser", "ko": "에이전트 클라우드 브라우저"},
    "human_in_agent_browser": {"en": "a person in the agent's browser", "ko": "에이전트 브라우저 안의 사람"},
    "human_own_browser": {"en": "a person's own browser", "ko": "사람의 자기 브라우저"},
}
LAYER_WORDS = {
    "site": {"en": "site", "ko": "사이트"},
    "platform": {"en": "agent product", "ko": "에이전트 제품"},
    "account": {"en": "account and sign-in", "ko": "계정·로그인"},
    "agent_env": {"en": "agent browser environment", "ko": "에이전트 브라우저 환경"},
    "task": {"en": "task and approval", "ko": "작업·승인"},
}
REASON_WORDS = {
    ("site", "cause"): {"en": "AHN's stored measurements for this site point to it", "ko": "AHN 이 저장한 이 사이트 측정이 여기를 가리켜요"},
    ("site", "robots_block"): {"en": "the site's robots.txt blocks the agents AHN checked", "ko": "사이트의 robots.txt 가 AHN 이 확인한 에이전트를 막아요"},
    ("site", "provider_incident"): {"en": "the provider's status page shows an announced incident", "ko": "공급사 상태 페이지에 발표된 장애가 있어요"},
    ("site", "home_check"): {"en": "AHN's own home-page check got an answer other than a normal page", "ko": "AHN 의 홈 페이지 확인이 정상 페이지가 아닌 답을 받았어요"},
    ("site", "change_recorded"): {"en": "AHN recorded a change in the site's declared policy or home-page check", "ko": "AHN 이 사이트의 선언 정책이나 홈 페이지 확인에서 변화를 기록했어요"},
    ("platform", "stopped_before_site"): {"en": "the agent product stopped before opening the site, and its own documentation describes sites it does not open", "ko": "에이전트 제품이 사이트를 열기 전에 멈췄고, 제품 공식 문서가 열지 않는 사이트를 설명해요"},
    ("platform", "stopped_plain"): {"en": "the agent product stopped before opening the site", "ko": "에이전트 제품이 사이트를 열기 전에 멈췄어요"},
    ("platform", "tools_off_plain"): {"en": "the page opened but the agent product's tools did not work on it", "ko": "페이지는 열렸지만 에이전트 제품의 도구가 거기서 작동하지 않았어요"},
    ("platform", "message_documented"): {"en": "the message you chose is one the product's documentation describes", "ko": "고른 메시지는 제품 문서가 설명하는 문구예요"},
    ("platform", "tools_off"): {"en": "the page opened but the agent product's tools did not work on it, and its documentation describes how a site can be limited", "ko": "페이지는 열렸지만 에이전트 제품의 도구가 거기서 작동하지 않았고, 제품 문서가 사이트 제한 방식을 설명해요"},
    ("platform", "differs_from_browser"): {"en": "the same step works in your usual browser but not in the agent product", "ko": "같은 단계가 보통 브라우저에서는 되는데 에이전트 제품에서는 안 돼요"},
    ("account", "extra_verification"): {"en": "the step stopped at an approval or code that goes to the account holder's device", "ko": "비밀번호 뒤에 사람 기기로 가는 승인 단계에서 멈췄어요"},
    ("account", "company_login"): {"en": "the step stopped at a company sign-in or device check", "ko": "회사 로그인이나 기기 확인에서 멈췄어요"},
    ("account", "sign_in"): {"en": "the step stopped at sign-in", "ko": "로그인에서 멈췄어요"},
    ("account", "after_login"): {"en": "an action after signing in failed", "ko": "로그인 뒤의 행동이 실패했어요"},
    ("agent_env", "cloud_browser"): {"en": "you said the agent works in a cloud browser, not on the person's device", "ko": "에이전트가 사람 기기가 아니라 클라우드 브라우저에서 일한다고 했어요"},
    ("task", "asks_each_action"): {"en": "you said the product asks the person to approve each action here", "ko": "이 제품이 여기서 행동마다 사람에게 승인을 묻는다고 했어요"},
}
REASON_SHORT = {
    "verification_on_person_device": {"en": "the site sends an approval or code to the account holder's own device", "ko": "사이트가 당신 기기로 승인이나 코드를 보내는 단계예요"},
    "sign_in_is_the_account_holders": {"en": "signing in is the account holder's step", "ko": "로그인은 계정 주인의 단계예요"},
    "approval_is_the_persons_decision": {"en": "approving each action is the person's decision", "ko": "행동마다 승인할지는 사람이 정해요"},
    "company_it_decides": {"en": "only the company's IT team can decide this", "ko": "이건 회사 IT 팀만 정할 수 있어요"},
    "platform_admin_decides": {"en": "only the organization's administrator can decide this", "ko": "이건 조직 관리자만 정할 수 있어요"},
    "platform_refused_step": {"en": "the agent product stopped here, so the person decides this step", "ko": "에이전트 제품이 여기서 멈췄으니 이 단계는 사람이 정해요"},
    "server_hand_off": {"en": "AHN's next action also hands this step to the person", "ko": "AHN 의 다음 행동도 이 단계를 사람에게 넘기라고 해요"},
}
PERSON_REASON_ID = {
    "verification_on_person_device": "S-P-VERIFY-DEVICE", "sign_in_is_the_account_holders": "S-P-SIGN-IN",
    "approval_is_the_persons_decision": "S-P-APPROVAL", "company_it_decides": "S-P-COMPANY-IT",
    "platform_admin_decides": "S-P-PLATFORM-ADMIN", "platform_refused_step": "S-P-PLATFORM-REFUSED",
    "server_hand_off": "S-P-SERVER-HAND-OFF",
}

# --- host_access_changes (D75) codes as words. A code not in a table leaves its sentence out (never a database string). ----
FIELD_WORDS = {
    "robots_status": {"en": "robots.txt read result", "ko": "robots.txt 읽기 결과"},
    "llms_presence": {"en": "llms.txt", "ko": "llms.txt"},
    "tdm_root": {"en": "text-and-data-mining reservation", "ko": "텍스트·데이터 마이닝 유보"},
    "site_declared_api": {"en": "standard files for agents", "ko": "에이전트용 표준 파일"},
    "site_declared_reservation": {"en": "standard files for agents", "ko": "에이전트용 표준 파일"},
    "home_outcome": {"en": "home-page check result", "ko": "홈 페이지 확인 결과"},
    "bot_vendor": {"en": "bot-defense marker on the home page", "ko": "홈 페이지의 봇 방어 표지"},
}
ROBOTS_VERDICT_FIELD = {"en": "robots.txt rule for {token_words}", "ko": "{token_words} 에 대한 robots.txt 규칙"}
OUR_TOKEN_WORDS = {"en": "AHN's own reader", "ko": "AHN 자신의 읽기 프로그램"}
CODE_WORDS: dict[str, dict[str, dict[str, str]]] = {
    "robots_status": {
        "ok": {"en": "read", "ko": "읽힘"}, "absent": {"en": "no file", "ko": "파일 없음"},
        "redirected": {"en": "redirected", "ko": "리디렉션"}, "refused": {"en": "refused", "ko": "거절"},
        "invalid": {"en": "unparseable", "ko": "해석 불가"}, "too_large": {"en": "too large", "ko": "너무 큼"},
        "not_public": {"en": "not public", "ko": "공개 아님"}},
    "llms_presence": {"present": {"en": "present", "ko": "있음"}, "absent": {"en": "absent", "ko": "없음"}},
    "tdm_root": {"reserved": {"en": "reserved", "ko": "유보"}, "open": {"en": "open", "ko": "열림"},
                 "unset": {"en": "unset", "ko": "미지정"}, "none": {"en": "none declared", "ko": "선언 없음"}},
    "site_declared_api": {"declared": {"en": "declared", "ko": "선언됨"}, "none": {"en": "none declared", "ko": "선언 없음"}},
    "site_declared_reservation": {"reserved": {"en": "reserved", "ko": "유보"}, "none": {"en": "none declared", "ko": "선언 없음"}},
    "home_outcome": {k: v for k, v in HOME_OUTCOME.items()},
    "bot_vendor": {**{k: {"en": v, "ko": v} for k, v in BOT_VENDOR.items()}, "none": {"en": "no marker", "ko": "표지 없음"}},
    "robots_verdict": {"allow": {"en": "allowed", "ko": "허용"}, "block": {"en": "blocked", "ko": "차단"}},
}

# --- words the answer must never use (checked on the templates and fixed phrases by tests) ---------------------------------
FORBIDDEN_EN = (
    "bypass", "workaround", "get around", "circumvent", "different user agent", "user-agent string", "spoof", "vpn", "proxy",
    "incognito", "private window", "clear cookies", "clear your cookies", "import cookies", "import session", "probably",
    "likely", "is a financial", "is a banking", "is an adult", "is a high-risk", "violat", "allowed to automate", "permitted",
    "safe to", "trusted", "the cause is", "because of", "the site blocks cloud browsers", "datacenter ip", "residential",
    "the push was blocked", "push notification failed because", "use another agent", "switch to", "try a different agent",
    "other agents confirmed", "it is known that", "no person needed", "you can continue without the user",
    "works for other agents", "solve the captcha", "solve the check", "sign in for you", "sign in for the user",
)
FORBIDDEN_KO = (
    "우회", "피해 가", "다른 ua", "시크릿 창", "쿠키를 지우", "쿠키를 가져오", "아마", "가능성이 높", "위반", "허용된 작업",
    "안전한 사이트", "원인은", "때문이에요", "클라우드 브라우저를 막", "데이터센터", "ip 때문", "다른 에이전트로", "다른 제품으로",
    "다른 에이전트가 확인했", "알려진", "사람 없이", "다른 에이전트는 돼요", "이 사이트는",
)
# "falls under" is allowed only in the sentence that says AHN does not judge it.
NEGATED_ONLY = ("falls under",)


def forbidden_in(text: str, lang: str) -> list[str]:
    """The forbidden words found in a fixed text (case-insensitive). "falls under" passes only after "does not judge whether"."""
    low = text.lower()
    found = [w for w in (FORBIDDEN_EN if lang == "en" else FORBIDDEN_KO) if w in low]
    if lang == "en" and "falls under" in low and "does not judge whether" not in low:
        found.append("falls under")
    return found


def fixed_phrases() -> list[tuple[str, str, str]]:
    """(label, lang, text) of every fixed phrase table value, for the tests' word check."""
    out: list[tuple[str, str, str]] = []

    def add(label: str, table: Any) -> None:
        if isinstance(table, dict):
            if set(table) == {"en", "ko"}:
                for lang in ("en", "ko"):
                    out.append((label, lang, table[lang]))
            else:
                for key, value in table.items():
                    add(f"{label}.{key}", value)

    for name, table in (("TOKEN_SCOPE", TOKEN_SCOPE), ("ADMIN_MARK", ADMIN_MARK), ("ROBOTS_STATE", ROBOTS_STATE),
                        ("UNREAD_REASON", UNREAD_REASON), ("INDICATOR", INDICATOR), ("HOME_OUTCOME", HOME_OUTCOME),
                        ("VENDOR_PART", VENDOR_PART), ("AUTH_WORDS", AUTH_WORDS), ("MECHANISM", MECHANISM),
                        ("SINCE_WORDS", SINCE_WORDS), ("STEP_WORDS", STEP_WORDS), ("OUTCOME_WORDS", OUTCOME_WORDS),
                        ("ENV_WORDS", ENV_WORDS), ("LAYER_WORDS", LAYER_WORDS), ("REASON_SHORT", REASON_SHORT),
                        ("FIELD_WORDS", FIELD_WORDS), ("CODE_WORDS", CODE_WORDS), ("ROBOTS_VERDICT_FIELD", ROBOTS_VERDICT_FIELD),
                        ("OUR_TOKEN_WORDS", OUR_TOKEN_WORDS)):
        add(name, table)
    for key, table in REASON_WORDS.items():
        add(f"REASON_WORDS.{key[0]}.{key[1]}", table)
    add("STOP_WORDS", STOP_WORDS)
    for lang, (lower, cap) in DEFAULT_PLATFORM.items():
        out.append(("DEFAULT_PLATFORM", lang, lower))
        out.append(("DEFAULT_PLATFORM", lang, cap))
    return out
