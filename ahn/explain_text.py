"""D108: the fixed sentences of GET /v1/why, English and Korean, and the fixed phrases that fill their blanks.

Every sentence is a template whose blanks are filled only from AHN's own data, the curated platform table or a fixed phrase
table below. No site text, no caller text and no database string is ever put into a sentence. ``tests/test_explain_templates.py``
checks that every template exists in both languages, that its blanks are in ``BLANKS``, that its words are not among the
forbidden ones, and that the longest filled text fits its limit.
"""

from __future__ import annotations

import re
from typing import Any

from .cause import UNNAMED_VENDORS
from .check_text import VENDOR_NAMES

# Blanks a template may use. A blank with no value means the sentence is left out; it is never rendered empty.
BLANKS = frozenset({
    "host", "date", "date2", "time", "time2", "platform", "platform_cap", "platform2", "vendor", "url", "quote", "quote2",
    "provider", "indicator", "token_scope", "state", "auth", "mechanism", "layer_words", "reason_words", "cause_line",
    "outcome_words", "vendor_part", "field_words", "from_words", "to_words", "n", "step_words", "env_words", "stop_words",
    "since", "person_line", "reason_short", "admin_mark", "unread_reason", "token_words", "cause_part",
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
   "{vendor} 공식 문서에는 어떤 robots.txt 토큰을 보내는지(보내기는 하는지) 적혀 있지 않아요(제품: {platform}). 그래서 어느 규칙 묶음이 적용되는지 AHN 이 말할 수 없어요.")
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
   "{provider} 상태 페이지 표시: {indicator}({time} 부터, AHN 읽기 {time2}).")
_t("S-L1D-ACTIVE-NOTIME",
   "{provider}'s status page shows {indicator} (AHN read it at {time2}); it gives no start time AHN can use.",
   "{provider} 상태 페이지 표시: {indicator}(AHN 읽기 {time2}). 쓸 수 있는 시작 시각은 적혀 있지 않아요.")
_t("S-L1D-CLEAR",
   "{provider}'s status page showed no announced incident when AHN read it at {time}. That does not confirm the site works.",
   "AHN 이 {time} 에 읽은 {provider} 상태 페이지에는 발표된 장애가 없었어요. 사이트가 정상이라는 확인은 아니에요.")
# D110: the overall indicator reads operational but the page lists an unresolved incident (the T6 reading of the lookup, D100).
_t("S-L1D-UNRESOLVED",
   "{provider}'s status page lists an unresolved incident, though its overall indicator reads operational (AHN read it at {time}). Check whether it touches your step.",
   "{provider} 상태 페이지의 전체 표시는 정상이지만 미해결 사건이 올라 있어요(AHN 읽기 {time}). 당신의 단계에 닿는지 확인해 보세요.")
_t("S-L1D-NONE",
   "AHN has no connected status feed for this site.",
   "AHN 에는 이 사이트의 상태 피드가 연결돼 있지 않아요.")
_t("S-L1E-CHANGED",
   "On {date}, AHN recorded a change in {host}'s {field_words}: {from_words} to {to_words}.",
   "{date} 에 AHN 이 {host} 의 {field_words} 변화를 기록했어요: {from_words} 에서 {to_words} 로.")
# D110: a change of AHN's own home-page check says what AHN's request got, never what the site set. A bot-defense marker is
# recorded only when that request got a challenge, so "no marker" means no challenge that time, not that the site removed one.
_t("S-L1E-HOME-CHALLENGE-ON",
   "On {date}, AHN's home-page check of {host} started getting a {vendor} challenge. This is what AHN's own request got, not a site setting AHN read.",
   "{date} 에 AHN 의 {host} 홈 페이지 확인이 {vendor} 챌린지를 받기 시작했어요. AHN 자신의 요청이 받은 결과이지 사이트 설정을 읽은 것이 아니에요.")
_t("S-L1E-HOME-CHALLENGE-OFF",
   "On {date}, AHN's home-page check of {host} stopped getting a {vendor} challenge. This is what AHN's own request got, not a site setting AHN read.",
   "{date} 에 AHN 의 {host} 홈 페이지 확인이 {vendor} 챌린지를 받지 않게 됐어요. AHN 자신의 요청이 받은 결과이지 사이트 설정을 읽은 것이 아니에요.")
_t("S-L1E-HOME-CHALLENGE-SWAP",
   "On {date}, AHN's home-page check of {host} got a {to_words} challenge where it had got a {from_words} challenge. This is what AHN's own request got, not a site setting AHN read.",
   "{date} 에 AHN 의 {host} 홈 페이지 확인이 받은 챌린지가 바뀌었어요: {from_words} → {to_words}. AHN 자신의 요청이 받은 결과이지 사이트 설정을 읽은 것이 아니에요.")
_t("S-L1E-HOME-OUTCOME",
   "On {date}, AHN's home-page check of {host} got {to_words} where it had got {from_words}. This is what AHN's own request got.",
   "{date} 에 AHN 의 {host} 홈 페이지 확인 결과가 바뀌었어요: {from_words} → {to_words}. AHN 자신의 요청이 받은 결과예요.")
_t("S-L1E-FLAP",
   "AHN's own check results went back and forth in this period; this change alone does not show that the site changed.",
   "이 기간에 AHN 확인 결과가 흔들렸어요. 이 변화만으로 사이트가 바뀌었다고 볼 수 없어요.")
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
   "어떤 사이트를 제한하는지·로그인을 어떻게 다루는지 적은 공식 문서를 AHN 이 찾지 못했어요(회사: {vendor}, 제품: {platform}, {date} 확인). 제품({platform})의 규칙이 관련됐는지 AHN 은 말할 수 없어요.")
_t("S-L2-UNREAD",
   "{vendor} publishes a help page on this ({url}), but AHN could not read it (checked {date}): {unread_reason}.",
   "관련 도움말({url}, 발행: {vendor})이 있지만 AHN 은 읽지 못했어요({date} 확인): {unread_reason}.")
_t("S-L2-NOT-REVIEWED",
   "AHN has not reviewed {platform}'s documentation yet.",
   "AHN 은 아직 {platform} 문서를 검토하지 않았어요.")
_t("S-L2-NOLIST",
   "{platform} does not publish a full list of the sites it limits, and AHN does not try to rebuild one.",
   "제한하는 사이트의 전체 목록은 공개돼 있지 않고(제품: {platform}), AHN 은 그것을 복원하려 하지 않아요.")
_t("S-L2-MSG-MATCH",
   "You chose the message {platform}'s documentation shows when {mechanism}. If your screen shows it, that documentation is the place to look next.",
   "고른 메시지는 {platform} 문서가 \"{mechanism}\" 때 뜬다고 적은 문구예요. 화면에 그 문구가 보이면 다음으로 볼 곳은 그 문서예요.")
_t("S-L2-MSG-OTHER-PLATFORM",
   "That message is documented for {platform2}, not for {platform}. AHN does not use it for {platform}.",
   "그 메시지는 {platform2} 문서에 있는 문구예요(고른 제품: {platform}). 제품({platform}) 판단에는 쓰지 않아요.")

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
   "{date}: {outcome_words} at {step_words}{env_words}{cause_part}",
   "{date}: {step_words}에서 {outcome_words}{env_words}{cause_part}")
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
# D110: the server hands a public read to the person only because AHN's own reads could not reach the page.
_t("S-P-SERVER-HAND-OFF-OPEN",
   "AHN's next action hands this step to the person: AHN's own reads could not reach the page, and the person can open it themselves to see whether it loads for them.",
   "서버의 다음 행동이 이 단계를 사람에게 넘겨요. AHN 의 읽기가 페이지에 닿지 못했고, 사용자가 직접 열어 보면 열리는지 알 수 있어요.")
_t("S-P-TOOLS-OFF",
   "The page shows but the agent's tools could not act on it. The person can open the page and do the step themselves.",
   "페이지는 보이는데 에이전트 도구가 동작하지 못했어요. 사람이 직접 페이지를 열어 그 단계를 할 수 있어요.")
_t("S-P-CHECK-PERSON",
   "A check listed below is one only the person can do (for example, trying the step in their own browser). It does not mean the whole step is theirs.",
   "아래 확인 중에는 사람만 할 수 있는 것이 있어요(예: 자기 브라우저에서 같은 단계 해 보기). 단계 전체가 사람 몫이라는 뜻은 아니에요.")

# --- things to check (CHK) and what to tell a person (H) --------------------------------------------------------------
_t("CHK-BROWSER-COMPARE",
   "A person can try the same step once in their usual browser, signed in to the same account. The result tells the site layer apart from the agent and account layers. It is a check, not a way for the agent to continue.",
   "사람이 같은 계정으로 보통 브라우저에서 같은 단계를 한 번 해 보세요. 결과가 사이트 층과 에이전트·계정 층을 갈라 줘요. 확인용이지 에이전트가 계속하는 방법이 아니에요.")
# D112: the same check, worded for a public page (no sign-in) and for a stop at the platform layer (the person opens the page).
_t("CHK-BROWSER-COMPARE-PUBLIC",
   "A person can open the same public page once in their usual browser, without signing in. The result tells the site layer apart from the agent layer. It is a check, not a way for the agent to continue.",
   "사람이 로그인 없이 같은 공개 페이지를 보통 브라우저에서 한 번 열어 보세요. 결과가 사이트 층과 에이전트 층을 갈라 줘요. 확인용이지 에이전트가 계속하는 방법이 아니에요.")
_t("CHK-BROWSER-COMPARE-OPEN",
   "The user opens the page themselves or with another tool, once, signed in to the same account. The result tells the site layer apart from the agent's tool. It is a check, not a way for the agent to continue.",
   "사용자가 직접, 또는 다른 도구로 같은 계정에서 같은 페이지를 한 번 열어 보세요. 결과가 사이트 층과 에이전트의 도구를 갈라 줘요. 확인용이지 에이전트가 계속하는 방법이 아니에요.")
_t("CHK-BROWSER-COMPARE-OPEN-PUBLIC",
   "The user opens the same public page themselves or with another tool, once, without signing in. The result tells the site layer apart from the agent's tool. It is a check, not a way for the agent to continue.",
   "사용자가 직접, 또는 다른 도구로 로그인 없이 같은 공개 페이지를 한 번 열어 보세요. 결과가 사이트 층과 에이전트의 도구를 갈라 줘요. 확인용이지 에이전트가 계속하는 방법이 아니에요.")
# D113: the same check when AHN was not told what the task is (no task_context): the account is not assumed.
_t("CHK-BROWSER-COMPARE-NOCTX",
   "A person can open the same page once in their usual browser; sign in only if the task needs the user's account. The result tells the site layer apart from the agent layer. It is a check, not a way for the agent to continue.",
   "사람이 같은 페이지를 보통 브라우저에서 한 번 열어 보세요. 작업이 사용자 계정을 쓸 때만 로그인하세요. 결과가 사이트 층과 에이전트 층을 갈라 줘요. 확인용이지 에이전트가 계속하는 방법이 아니에요.")
_t("CHK-BROWSER-COMPARE-OPEN-NOCTX",
   "The user opens the same page themselves or with another tool, once; signing in only if the task needs their account. The result tells the site layer apart from the agent's tool. It is a check, not a way for the agent to continue.",
   "사용자가 직접, 또는 다른 도구로 같은 페이지를 한 번 열어 보세요. 작업이 사용자 계정을 쓸 때만 로그인하세요. 결과가 사이트 층과 에이전트의 도구를 갈라 줘요. 확인용이지 에이전트가 계속하는 방법이 아니에요.")
# D112: at the platform layer the check is the tool's own policy and settings, or the user opening the page; the lookup's developer path is not it.
_t("CHK-PLATFORM-LOOK",
   "Check the tool's own policy and settings for this kind of page; the user can also open the page themselves.",
   "그 도구의 정책과 설정을 확인해 보세요. 사용자가 직접 페이지를 열어 볼 수도 있어요.")
_t("CHK-MSG-MATCH",
   "Compare the message on screen with the messages {platform}'s documentation lists ({url}).",
   "화면 메시지를 {platform} 문서({url})가 적은 메시지와 비교해 보세요.")
_t("CHK-ORG-ADMIN",
   "If {platform} comes through an organization plan, ask its administrator whether a site rule covers {host} ({url}).",
   "조직 플랜으로 쓰는 제품이면(제품: {platform}) 관리자에게 {host} 에 사이트 규칙이 걸려 있는지 물어보세요({url}).")
_t("CHK-SAFETY-CONFIRM",
   "If {platform} asks for approval at each action, its documentation describes that step ({url}). Approving is the person's own decision.",
   "제품이 행동마다 승인을 묻는다면(제품: {platform}) 그 절차는 문서({url})에 있어요. 승인 여부는 사람이 정해요.")
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
   "AHN 이 {date} 에 {platform} 의 페이지가 바뀐 것을 봤어요({url}). 앱 버전과 그 페이지를 확인해 보세요.")
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
# D113: the same item (id CHK-OFFICIAL-PATH) when the address is one of the site's customer pages (official_paths.consumer), not a developer path.
_t("CHK-OFFICIAL-PATH-CUSTOMER",
   "The site's own customer page is {url}.",
   "사이트 자체 고객 페이지는 {url} 이에요.")
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
   "{since} 부터 {host} 에서 \"{stop_words}\" 로 멈춰요(도구: {platform}). 우리 조직의 {platform} 사이트 정책이 {host} 에 걸려 있나요?")
_t("H-PLATFORM",
   "Since {since}, {platform} stops on {host} at: {stop_words}, while the same step works in a usual browser. App version: (fill in).",
   "{since} 부터 {host} 에서 \"{stop_words}\" 로 멈추고(도구: {platform}), 같은 단계는 보통 브라우저에서 돼요. 앱 버전: (직접 적기).")
_t("H-SITE",
   "Since {since}, a step on {host} stops at: {stop_words}, in every browser I tried.",
   "{since} 부터 {host} 의 단계가 제가 써 본 모든 브라우저에서 \"{stop_words}\" 로 멈춰요.")
_t("H-USER",
   "{platform_cap} could not finish this step on {host} (stopped at: {stop_words}). {person_line}",
   "이 단계를 {host} 에서 끝내지 못했어요(\"{stop_words}\") — 도구: {platform_cap}. {person_line}")
_t("H-USER-NOHOST",
   "{platform_cap} could not finish this step (stopped at: {stop_words}). {person_line}",
   "이 단계를 끝내지 못했어요(\"{stop_words}\") — 도구: {platform_cap}. {person_line}")
_t("H-USER-YES",
   "You need to do this step yourself: {reason_short}.",
   "이 단계는 직접 하셔야 해요: {reason_short}.")
_t("H-USER-NO",
   "AHN found no cause it can confirm; see the checks listed.",
   "AHN 이 확인한 범위에서는 짚이는 곳이 없었어요. 확인 목록을 따라가 보세요.")
_t("H-USER-OPEN",
   "You can open the page yourself to see whether it loads for you; AHN's own reads could not reach it.",
   "직접 페이지를 열어 보면 열리는지 알 수 있어요. AHN 의 읽기는 페이지에 닿지 못했어요.")
_t("H-USER-CHECK",
   "One of the checks needs you; AHN does not say the whole step is yours.",
   "확인 하나는 직접 하셔야 해요. 단계 전체가 당신 몫이라는 뜻은 아니에요.")
# D110: in a Korean answer, server.summary_line is the lookup's own sentence and stays in English; this says so.
_t("S-G-SERVER-EN",
   "server.summary_line is the lookup's own sentence and is in English.",
   "server.summary_line 은 조회 서버의 원문이에요(서버 원문, 영어). 한국어 안내는 이 답의 다른 문장에 있어요.")
# D114: the lookup block of a Korean answer kept a next_checks or cause sentence that has no Korean version in English.
_t("S-G-SERVER-EN-PARTS",
   "Some sentences of the server block (summary_line, cause, next_checks) are the lookup's own and are in English.",
   "server 블록의 일부 문장(summary_line·cause·next_checks)은 조회 서버의 원문이에요(서버 원문, 영어). 한국어 안내는 이 답의 다른 문장에 있어요.")
# D112: when the lookup's action is a developer path that the explanation's own first layer does not point to, the lookup block moves to the end
# and says what it is.
_t("S-G-SERVER-CONTEXT",
   "This block is a summary of the plain lookup answer, given without your step's context; its full text is at links.check.",
   "이 블록은 단계 맥락 없이 낸 조회 답의 요약이고, 전문은 links.check 에 있어요.")
# D113: AHN was not told the task (no task_context), so the account is not assumed.
_t("S-G-NOCTX",
   "AHN was not told what the task is; the lookup's sentences that start with 'For a task in the user's own account' apply only if it is.",
   "AHN 은 무슨 작업인지 듣지 못했어요. 조회 문장 중 '사용자 본인 계정의 일에는'(조회 문장 원문은 \"For a task in the user's own account\")으로 시작하는 것은 그런 작업일 때만 해당돼요.")
_t("TL-PLATFORM-DOC",
   "On {date}, AHN saw {platform}'s page {url} change.",
   "{date} 에 AHN 이 {platform} 의 페이지가 바뀐 것을 봤어요({url}).")

# D112: Korean versions of the lookup's own summary_line, for a Korean answer. The lookup's line is built from the fixed English sentences of
# ahn/response_guide.py (one or more, joined by a space), so the table is per sentence: the key is one English sentence exactly as the lookup
# gives it, with "{n}" (seconds) and "{m}" (minutes) standing for the one whole number it carries; the Korean value carries the same token.
# A line is translated only when every one of its sentences has an entry (summary_line_ko); otherwise the whole line stays English and the
# answer says so (S-G-SERVER-EN). A test checks that every sentence in response_guide.py has an entry, so a new sentence needs its Korean here.
_SUMMARY_PAIRS = (
    ("A customer page is listed (next_action.customer_url) for tasks a person does on the site.",
     "사람이 사이트에서 하는 일에는 고객 페이지가 올라 있어요(next_action.customer_url)."),
    ("A developer API is also listed (official_paths).", "개발자 API 도 올라 있어요(official_paths)."),
    ("A developer API is listed (official_paths) for API work; for pages, follow your operator's rules and tell your user.",
     "API 작업용 개발자 API 가 올라 있어요(official_paths). 페이지 작업은 운영자의 규칙을 따르고 사용자에게 알리세요."),
    ("A developer API is listed (official_paths) for API work; for pages, tell your user.",
     "API 작업용 개발자 API 가 올라 있어요(official_paths). 페이지 작업은 사용자에게 알리세요."),
    ("A provider status feed is listed (provider_status), but it does not give a clear current state here; check the status page at next_action.status_url.",
     "공급자 상태 피드가 올라 있지만(provider_status) 지금은 분명한 현재 상태를 주지 않아요. next_action.status_url 의 상태 페이지에서 확인하세요."),
    ("A provider status feed is listed (provider_status), but it gives no clear current state, and the status page did not open for you.",
     "공급자 상태 피드가 올라 있지만(provider_status) 분명한 현재 상태를 주지 않고, 상태 페이지도 당신에게 열리지 않았어요."),
    ("AHN cannot judge a single page's 5xx, and could not read the site's robots.txt.",
     "AHN 은 페이지 하나의 5xx 를 판단할 수 없고, 사이트의 robots.txt 도 읽지 못했어요."),
    ("AHN cannot judge a single page's 5xx, and has no declared policy for this host.",
     "AHN 은 페이지 하나의 5xx 를 판단할 수 없고, 이 호스트에 대해 선언된 정책도 없어요."),
    ("AHN cannot judge a single page's 5xx.", "AHN 은 페이지 하나의 5xx 를 판단할 수 없어요."),
    ("AHN cannot judge a single page's 5xx; the site also refused AHN's plain robots.txt read.",
     "AHN 은 페이지 하나의 5xx 를 판단할 수 없고, 사이트가 AHN 의 일반 robots.txt 읽기도 거절했어요."),
    ("AHN could not read this provider's status feed just now (provider_status), so it cannot say whether the site is up.",
     "AHN 이 방금 이 공급자의 상태 피드를 읽지 못해서(provider_status) 사이트가 열려 있는지 말할 수 없어요."),
    ("AHN could not read this provider's status feed just now, and the status page did not open for you.",
     "AHN 이 방금 이 공급자의 상태 피드를 읽지 못했고, 상태 페이지도 당신에게 열리지 않았어요."),
    ("AHN knows no official status page for this site, so it cannot say whether it is up.",
     "AHN 은 이 사이트의 공식 상태 페이지를 알지 못해서 열려 있는지 말할 수 없어요."),
    ("AHN knows no official status page for this site, so it cannot say whether it is up; tell your user its status is unverified.",
     "AHN 은 이 사이트의 공식 상태 페이지를 알지 못해서 열려 있는지 말할 수 없어요. 상태를 확인하지 못했다고 사용자에게 알리세요."),
    ("AHN reads no status feed for this site, and its status page did not open for you, so AHN cannot say whether it is up.",
     "AHN 은 이 사이트의 상태 피드를 읽지 않고, 상태 페이지도 당신에게 열리지 않아서 열려 있는지 말할 수 없어요."),
    ("AHN reads no status feed for this site, so it cannot say whether it is up.",
     "AHN 은 이 사이트의 상태 피드를 읽지 않아서 열려 있는지 말할 수 없어요."),
    ("AHN reads no status feed for this site, so it cannot say whether it is up; tell your user its status is unverified.",
     "AHN 은 이 사이트의 상태 피드를 읽지 않아서 열려 있는지 말할 수 없어요. 상태를 확인하지 못했다고 사용자에게 알리세요."),
    ("AHN reads no status feed for this site, so it cannot say whether it is up; the status page it knows covers only the provider's API.",
     "AHN 은 이 사이트의 상태 피드를 읽지 않아서 열려 있는지 말할 수 없어요. AHN 이 아는 상태 페이지는 공급자의 API 만 다뤄요."),
    ("AHN sees hosts, not pages, so it cannot judge this 5xx.", "AHN 은 페이지가 아니라 호스트를 보기 때문에 이 5xx 를 판단할 수 없어요."),
    ("API work: official_paths.", "API 작업은 official_paths 를 보세요."),
    ("An earlier read of the site's robots.txt failed and another is queued.",
     "앞서 사이트의 robots.txt 읽기가 실패했고 다음 읽기가 대기 중이에요."),
    ("An official developer API is listed with a no-sign-up option (official_paths.access.auth).",
     "공식 개발자 API 가 가입 없이 쓸 수 있는 방식으로 올라 있어요(official_paths.access.auth)."),
    ("An official developer API needs a key or sign-up (official_paths.access.auth).",
     "공식 개발자 API 에는 키나 가입이 필요해요(official_paths.access.auth)."),
    ("An official developer API or portal is listed; prefer it over page automation.",
     "공식 개발자 API 나 포털이 올라 있어요. 페이지 자동화보다 그쪽을 쓰세요."),
    ("An official developer path is listed for this site.", "이 사이트의 공식 개발자 경로가 올라 있어요."),
    ("An official developer path is listed; for API work use it, not page automation; for a task in the user's own account, hand the step to your user.",
     "공식 개발자 경로가 올라 있어요. API 작업에는 페이지 자동화 대신 그 경로를 쓰고, 사용자 본인 계정의 일은 그 단계를 사용자에게 넘기세요."),
    ("Check next_action.status_url before telling your user it is up.",
     "사용자에게 열려 있다고 말하기 전에 next_action.status_url 을 확인하세요."),
    ("Check next_action.status_url; it may be partly affected.", "next_action.status_url 을 확인하세요. 일부가 영향을 받고 있을 수 있어요."),
    ("Check the provider's own status page, or tell your user its status is unverified.",
     "공급자의 상태 페이지를 확인하거나, 상태를 확인하지 못했다고 사용자에게 알리세요."),
    ("Check the site itself, or tell your user its status is unverified.",
     "사이트 자체를 확인하거나, 상태를 확인하지 못했다고 사용자에게 알리세요."),
    ("Check the status page at next_action.status_url.", "next_action.status_url 의 상태 페이지를 확인하세요."),
    ("Check your tool and path; retry once later; if it persists, tell your user.",
     "도구와 경로를 확인하고 나중에 한 번 다시 시도하세요. 계속되면 사용자에게 알리세요."),
    ("Check your tool and path; retry once later; then tell your user.",
     "도구와 경로를 확인하고 나중에 한 번 다시 시도한 뒤 사용자에게 알리세요."),
    ("Check your tool; retry once later, then hand your user next_action.customer_url.",
     "도구를 확인하고 나중에 한 번 다시 시도한 뒤 사용자에게 next_action.customer_url 을 넘기세요."),
    ("Continue modestly unless yours is in access_profile.robots.agents_blocked.",
     "내 에이전트가 access_profile.robots.agents_blocked 에 없다면 요청을 조금씩만 하며 계속하세요."),
    ("Continue modestly; not an access guarantee, and site terms still apply.",
     "요청을 조금씩만 하며 계속하세요. 접근 보장은 아니고 사이트 약관도 여전히 적용돼요."),
    ("Continue modestly; not an access guarantee.", "요청을 조금씩만 하며 계속하세요. 접근 보장은 아니에요."),
    ("Continue unless yours is in robots.agents_blocked.", "내 에이전트가 robots.agents_blocked 에 없다면 계속하세요."),
    ("Continue with modest requests unless yours is in access_profile.robots.agents_blocked.",
     "내 에이전트가 access_profile.robots.agents_blocked 에 없다면 요청을 조금씩만 하며 계속하세요."),
    ("Continue with modest requests; declared policy is not an access guarantee.",
     "요청을 조금씩만 하며 계속하세요. 선언된 정책은 접근 보장이 아니에요."),
    ("Continue with modest requests; not an access guarantee, and the site's terms still apply.",
     "요청을 조금씩만 하며 계속하세요. 접근 보장은 아니고 사이트 약관도 여전히 적용돼요."),
    ("Continue with modest requests; not an access guarantee.", "요청을 조금씩만 하며 계속하세요. 접근 보장은 아니에요."),
    ("Continue with modest requests; paths below the root may differ, and this is not an access guarantee.",
     "요청을 조금씩만 하며 계속하세요. 루트 아래 경로는 다를 수 있고 접근 보장은 아니에요."),
    ("Continue with modest requests; this is not an access guarantee.", "요청을 조금씩만 하며 계속하세요. 접근 보장은 아니에요."),
    ("Do not poll.", "계속 되풀이해서 조회하지 마세요."),
    ("Do not read that as permission; for anything beyond the status question, use the listed official developer path.",
     "허락으로 읽지 마세요. 상태 확인 너머의 일에는 올라 있는 공식 개발자 경로를 쓰세요."),
    ("Do not read that as permission; tell the user before automating pages.",
     "허락으로 읽지 마세요. 페이지를 자동화하기 전에 사용자에게 알리세요."),
    ("Do not read that as permission; use the listed official developer path.",
     "허락으로 읽지 마세요. 올라 있는 공식 개발자 경로를 쓰세요."),
    ("Do not read this as permission.", "이것을 허락으로 읽지 마세요."),
    ("Do not switch tools or user agents; hand your user the site's customer page, next_action.customer_url.",
     "도구나 user agent 를 바꾸지 말고, 사용자에게 사이트의 고객 페이지(next_action.customer_url)를 넘기세요."),
    ("Do not switch tools or user agents; tell your user now, who can open the page themselves.",
     "도구나 user agent 를 바꾸지 말고 지금 사용자에게 알리세요. 사용자가 직접 페이지를 열어 볼 수 있어요."),
    ("Do not switch tools; hand your user the step and next_action.customer_url.",
     "도구를 바꾸지 말고 사용자에게 그 단계와 next_action.customer_url 을 넘기세요."),
    ("Do not switch tools; hand your user the step and the site's customer page, next_action.customer_url.",
     "도구를 바꾸지 말고 사용자에게 그 단계와 사이트의 고객 페이지(next_action.customer_url)를 넘기세요."),
    ("Either way, AHN cannot judge this 5xx.", "어느 쪽이든 AHN 은 이 5xx 를 판단할 수 없어요."),
    ("Either way, do not switch tools or user agents to retry after a refusal; tell your user.",
     "어느 쪽이든 거절당한 뒤에 도구나 user agent 를 바꿔 다시 시도하지 말고 사용자에게 알리세요."),
    ("Either way, no response came; check your tool, retry once later, then tell your user.",
     "어느 쪽이든 응답이 오지 않았어요. 도구를 확인하고 나중에 한 번 다시 시도한 뒤 사용자에게 알리세요."),
    ("Else continue modestly.", "아니면 요청을 조금씩만 하며 계속하세요."),
    ("Else continue.", "아니면 계속하세요."),
    ("Follow your operator's rules and the site's terms.", "운영자의 규칙과 사이트 약관을 따르세요."),
    ("Follow your operator's rules.", "운영자의 규칙을 따르세요."),
    ("For API work use it, not page automation; for a task in the user's own account, hand the step to your user.",
     "API 작업에는 페이지 자동화 대신 그것을 쓰고, 사용자 본인 계정의 일은 그 단계를 사용자에게 넘기세요."),
    ("For API work use one you have or ask before signing up; for a task in the user's own account, hand the step to your user.",
     "API 작업에는 이미 가진 키를 쓰거나 가입하기 전에 먼저 물어보고, 사용자 본인 계정의 일은 그 단계를 사용자에게 넘기세요."),
    ("For a task in the user's own account, use the site's customer page (next_action.customer_url) instead of retrying page automation.",
     "사용자 본인 계정의 일에는 페이지 자동화를 다시 시도하는 대신 사이트의 고객 페이지(next_action.customer_url)를 쓰세요."),
    ("For a task in the user's own account, use the site's customer page (next_action.customer_url).",
     "사용자 본인 계정의 일에는 사이트의 고객 페이지(next_action.customer_url)를 쓰세요."),
    ("For anything beyond the status question, use the listed official developer path.",
     "상태 확인 너머의 일에는 올라 있는 공식 개발자 경로를 쓰세요."),
    ("For tasks in the user's own account, the site's customer page is next_action.customer_url.",
     "사용자 본인 계정의 일에는 사이트의 고객 페이지가 next_action.customer_url 이에요."),
    ("Hand it to the user through your normal handoff; do not try to solve or bypass the check.",
     "평소 인계 방법으로 사용자에게 넘기고, 확인을 풀거나 피하려고 하지 마세요."),
    ("Hand it to the user; never ask for, store or pass on credentials.",
     "사용자에게 넘기세요. 자격 증명은 요구하거나 저장하거나 전달하지 마세요."),
    ("If a page refused you, tell your user; else continue.", "페이지가 당신을 거절했다면 사용자에게 알리고, 아니면 계속하세요."),
    ("If a page refused you, tell your user; for a task in their own account, hand them next_action.customer_url.",
     "페이지가 당신을 거절했다면 사용자에게 알리세요. 사용자 본인 계정의 일이면 next_action.customer_url 을 넘기세요."),
    ("If a page refused you, tell your user; for their own account, hand them next_action.customer_url.",
     "페이지가 당신을 거절했다면 사용자에게 알리세요. 본인 계정의 일이면 next_action.customer_url 을 넘기세요."),
    ("If a page refused you, that came from your tool, the site's edge or a login; tell your user.",
     "페이지가 당신을 거절했다면 그것은 도구, 사이트의 edge, 로그인 중 하나에서 온 것이니 사용자에게 알리세요."),
    ("If a page refused you, the refusal came from your tool, the site's edge or a login; tell your user.",
     "페이지가 당신을 거절했다면 그 거절은 도구, 사이트의 edge, 로그인 중 하나에서 온 것이니 사용자에게 알리세요."),
    ("If a service you use is affected, wait; look up again in about {m} minutes.",
     "쓰는 서비스가 영향을 받는다면 기다리고 약 {m}분 뒤에 다시 조회하세요."),
    ("If its status page dates it recently and names a service you use, wait; otherwise continue.",
     "상태 페이지에 최근 날짜가 있고 쓰는 서비스가 적혀 있다면 기다리고, 아니면 계속하세요."),
    ("If its status page names a service you use, wait; look up again in about {m} minutes.",
     "상태 페이지에 쓰는 서비스가 적혀 있다면 기다리고 약 {m}분 뒤에 다시 조회하세요."),
    ("If refused, tell your user and hand them next_action.customer_url.",
     "거절당했다면 사용자에게 알리고 next_action.customer_url 을 넘기세요."),
    ("If your requests send one in next_action.stop_if_you_are, stop retrying pages; else send agent_vendor.",
     "요청이 next_action.stop_if_you_are 의 항목 중 하나를 보낸다면 페이지 재시도를 멈추고, 아니면 agent_vendor 를 보내세요."),
    ("If yours is in access_profile.robots.agents_blocked, stop.", "내 에이전트가 access_profile.robots.agents_blocked 에 있다면 멈추세요."),
    ("If yours is one, stop retrying pages; else send agent_vendor.",
     "내 것이 그중 하나라면 페이지 재시도를 멈추고, 아니면 agent_vendor 를 보내세요."),
    ("Its official status page is next_action.status_url; check it there.", "공식 상태 페이지는 next_action.status_url 이에요. 거기서 확인하세요."),
    ("Its status page, next_action.status_url, covers only the provider's API, not the whole site; check it there for API status.",
     "상태 페이지(next_action.status_url)는 사이트 전체가 아니라 공급자의 API 만 다뤄요. API 상태는 거기서 확인하세요."),
    ("Look up next_action.lookup_host once, with the same fields you sent, for the site-wide declaration.",
     "보낸 것과 같은 항목으로 next_action.lookup_host 를 한 번 조회해서 사이트 전체의 선언을 확인하세요."),
    ("Look up once more in {n} s; if the answer is still pending then, treat the policy as unknown.",
     "{n}초 뒤에 한 번 더 조회하세요. 그때도 답이 대기 중이면 정책을 알 수 없는 것으로 보세요."),
    ("More by topic: official_paths.consumer.", "주제별로 더 보려면 official_paths.consumer 를 보세요."),
    ("No declared policy could be read for this host and no official path is listed.",
     "이 호스트에 대해 선언된 정책을 읽지 못했고 올라 있는 공식 경로도 없어요."),
    ("No declared policy could be read for this host.", "이 호스트에 대해 선언된 정책을 읽지 못했어요."),
    ("No declared policy or official path is available for this host.", "이 호스트에는 선언된 정책도 공식 경로도 없어요."),
    ("No official path is listed; follow your operator's rules.", "올라 있는 공식 경로가 없어요. 운영자의 규칙을 따르세요."),
    ("No response came (timeout or connection), and AHN could not read robots.txt.",
     "응답이 오지 않았고(시간 초과 또는 연결), AHN 도 robots.txt 를 읽지 못했어요."),
    ("No response came (timeout or connection), and AHN could not read the site's robots.txt either.",
     "응답이 오지 않았고(시간 초과 또는 연결), AHN 도 사이트의 robots.txt 를 읽지 못했어요."),
    ("No response came (timeout or connection), and AHN has no declared policy for this host.",
     "응답이 오지 않았고(시간 초과 또는 연결), AHN 에는 이 호스트에 대해 선언된 정책이 없어요."),
    ("No response came (timeout or connection), and the site refused AHN's plain robots.txt read too.",
     "응답이 오지 않았고(시간 초과 또는 연결), 사이트가 AHN 의 일반 robots.txt 읽기도 거절했어요."),
    ("No response came (timeout or connection).", "응답이 오지 않았어요(시간 초과 또는 연결)."),
    ("Nothing here forbids the step; follow your operator's rules and the site's terms.",
     "여기에는 이 단계를 금지하는 것이 없어요. 운영자의 규칙과 사이트 약관을 따르세요."),
    ("Official path listed; use it instead of page automation.", "공식 경로가 올라 있어요. 페이지 자동화 대신 그것을 쓰세요."),
    ("Page automation may be refused too; for anything beyond the status question, use the listed official developer path.",
     "페이지 자동화도 거절될 수 있어요. 상태 확인 너머의 일에는 올라 있는 공식 개발자 경로를 쓰세요."),
    ("Page automation may be refused too; use the listed official developer path.",
     "페이지 자동화도 거절될 수 있어요. 올라 있는 공식 개발자 경로를 쓰세요."),
    ("Page automation may be refused; hand off if needed.", "페이지 자동화는 거절될 수 있어요. 필요하면 사용자에게 넘기세요."),
    ("Policy is read only for a site's main domain, not this subdomain.", "정책은 사이트의 주 도메인에서만 읽고, 이 하위 도메인에서는 읽지 않아요."),
    ("Prefer that documented API or portal over page automation for API work; for a task in the user's own account, hand the step to your user.",
     "API 작업에는 문서화된 그 API 나 포털을 페이지 자동화보다 우선 쓰고, 사용자 본인 계정의 일은 그 단계를 사용자에게 넘기세요."),
    ("Retry once later; if it persists, tell your user and hand them next_action.customer_url.",
     "나중에 한 번 다시 시도하고, 계속되면 사용자에게 알리며 next_action.customer_url 을 넘기세요."),
    ("Retry once later; if it persists, tell your user.", "나중에 한 번 다시 시도하세요. 계속되면 사용자에게 알리세요."),
    ("Stop automated retries; hand the step to the user if needed.", "자동 재시도를 멈추고 필요하면 그 단계를 사용자에게 넘기세요."),
    ("Stop retrying pages and use the listed official developer path instead.",
     "페이지 재시도를 멈추고 대신 올라 있는 공식 개발자 경로를 쓰세요."),
    ("Stop retrying pages; for anything beyond the status question, use the listed official developer path.",
     "페이지 재시도를 멈추세요. 상태 확인 너머의 일에는 올라 있는 공식 개발자 경로를 쓰세요."),
    ("Stop retrying pages; use the listed official path.", "페이지 재시도를 멈추고 올라 있는 공식 경로를 쓰세요."),
    ("Tell your user now and hand them next_action.customer_url; they can open the page themselves.",
     "지금 사용자에게 알리고 next_action.customer_url 을 넘기세요. 사용자가 직접 페이지를 열어 볼 수 있어요."),
    ("Tell your user now; they can open the page themselves.", "지금 사용자에게 알리세요. 사용자가 직접 페이지를 열어 볼 수 있어요."),
    ("Tell your user the service may be partly affected.", "서비스 일부가 영향을 받을 수 있다고 사용자에게 알리세요."),
    ("Tell your user the site's status is unverified.", "사이트의 상태를 확인하지 못했다고 사용자에게 알리세요."),
    ("Tell your user the status is unverified.", "상태를 확인하지 못했다고 사용자에게 알리세요."),
    ("That covers the provider's own services, not your account; its page is next_action.status_url.",
     "이것은 공급자 자체 서비스를 다룰 뿐 당신의 계정은 아니에요. 그 페이지는 next_action.status_url 이에요."),
    ("That status page did not open for you; give your user this reading, not a guess.",
     "그 상태 페이지는 당신에게 열리지 않았어요. 추측이 아니라 이 판독을 사용자에게 전하세요."),
    ("The API may not reach the user's own account; hand the user next_action.customer_url.",
     "API 는 사용자 본인 계정에 닿지 않을 수 있어요. 사용자에게 next_action.customer_url 을 넘기세요."),
    ("The API may not reach the user's own account; the site's page is next_action.customer_url.",
     "API 는 사용자 본인 계정에 닿지 않을 수 있어요. 사이트의 페이지는 next_action.customer_url 이에요."),
    ("The customer page is on the same site and may not open either; tell your user now and hand them next_action.customer_url.",
     "고객 페이지는 같은 사이트에 있어서 그것도 열리지 않을 수 있어요. 지금 사용자에게 알리고 next_action.customer_url 을 넘기세요."),
    ("The provider has posted no incident, only maintenance in progress on some regions or points of presence.",
     "공급자는 장애를 공지하지 않았고, 일부 지역이나 접속 거점에서 진행 중인 점검만 있어요."),
    ("The provider has posted no incident, only maintenance in progress, while its status page shows an issue.",
     "공급자는 장애를 공지하지 않았고 진행 중인 점검만 있지만, 상태 페이지에는 문제가 보여요."),
    ("The provider reports an active issue.", "공급자가 진행 중인 문제를 알리고 있어요."),
    ("The provider reports an issue that may reach only some regions.", "공급자가 일부 지역에만 닿을 수 있는 문제를 알리고 있어요."),
    ("The provider reports scheduled maintenance.", "공급자가 예정된 점검을 알리고 있어요."),
    ("The provider shows a minor issue with no update time we can read, so it may be old.",
     "공급자가 경미한 문제를 보이지만 읽을 수 있는 갱신 시각이 없어서 오래된 것일 수 있어요."),
    ("The provider's incident history lists an open incident but no severity or affected services.",
     "공급자의 장애 이력에 열린 장애가 있지만 심각도나 영향받는 서비스는 없어요."),
    ("The provider's status feed reports no active issue (provider_status).", "공급자의 상태 피드는 진행 중인 문제가 없다고 알려요(provider_status)."),
    ("The provider's status feed reports no active issue (provider_status, see its fetched_at).",
     "공급자의 상태 피드는 진행 중인 문제가 없다고 알려요(provider_status, fetched_at 참고)."),
    ("The provider's status feed's overall indicator reads operational, but AHN did not read its incident list.",
     "공급자 상태 피드의 전체 지표는 정상이지만, AHN 은 그 장애 목록을 읽지 않았어요."),
    ("The provider's status page lists an unresolved incident (provider_status.active_incidents), and that page did not open for you.",
     "공급자의 상태 페이지에 해결되지 않은 장애가 올라 있고(provider_status.active_incidents) 그 페이지가 당신에게 열리지 않았어요."),
    ("The provider's status page lists an unresolved incident (provider_status.active_incidents), though its overall indicator reads operational.",
     "공급자의 상태 페이지에 해결되지 않은 장애가 올라 있어요(provider_status.active_incidents). 다만 전체 지표는 정상으로 나와요."),
    ("The provider's status page shows a major or critical problem beyond any posted incident.",
     "공급자의 상태 페이지에 공지된 장애 밖의 큰 문제나 심각한 문제가 보여요."),
    ("The site refused even a plain automated robots.txt read, and no official path is listed.",
     "사이트가 일반 자동화 robots.txt 읽기마저 거절했고, 올라 있는 공식 경로도 없어요."),
    ("The site refused even a plain automated robots.txt read.", "사이트가 일반 자동화 robots.txt 읽기마저 거절했어요."),
    ("The site refused even a plain robots.txt read; pages may refuse too.", "사이트가 일반 robots.txt 읽기마저 거절했어요. 페이지도 거절할 수 있어요."),
    ("The site's edge refused both you and AHN's own check (ahn_check).", "사이트의 edge 가 당신과 AHN 의 자체 확인(ahn_check)을 모두 거절했어요."),
    ("The site's edge refused both you and AHN's own home-page check (ahn_check).",
     "사이트의 edge 가 당신과 AHN 의 자체 홈페이지 확인(ahn_check)을 모두 거절했어요."),
    ("The site's edge refused both you and AHN's plain robots.txt read.", "사이트의 edge 가 당신과 AHN 의 일반 robots.txt 읽기를 모두 거절했어요."),
    ("The site's edge refused you, and AHN's last home-page check (over a day ago) was refused too.",
     "사이트의 edge 가 당신을 거절했고, AHN 의 마지막 홈페이지 확인(하루 넘게 전)도 거절됐어요."),
    ("The site's robots.txt blocks agent user agents at the root.", "사이트의 robots.txt 가 루트에서 에이전트 user agent 를 막아요."),
    ("The site's robots.txt blocks agent user agents at the site root.", "사이트의 robots.txt 가 사이트 루트에서 에이전트 user agent 를 막아요."),
    ("The site's robots.txt blocks some agent tokens (next_action.stop_if_you_are).",
     "사이트의 robots.txt 가 일부 에이전트 토큰을 막아요(next_action.stop_if_you_are)."),
    ("The site's robots.txt blocks some agent tokens.", "사이트의 robots.txt 가 일부 에이전트 토큰을 막아요."),
    ("The site's robots.txt blocks some other agents, not the tokens checked for you.",
     "사이트의 robots.txt 가 다른 에이전트를 막지만, 당신에게 해당하는 것으로 확인한 토큰은 아니에요."),
    ("The site's robots.txt blocks your vendor's agent under another name.", "사이트의 robots.txt 가 당신 공급자의 에이전트를 다른 이름으로 막아요."),
    ("The site's robots.txt could not be read, so its agent policy is unknown, not permissive.",
     "사이트의 robots.txt 를 읽지 못해서 에이전트 정책을 알 수 없어요. 허용이라는 뜻이 아니에요."),
    ("The site's robots.txt could not be read, so no declared policy is known.", "사이트의 robots.txt 를 읽지 못해서 선언된 정책을 알 수 없어요."),
    ("The site's robots.txt disallows the root for agents it does not name (every token not in robots.agents_allowed), and no official path is listed.",
     "사이트의 robots.txt 가 이름을 정하지 않은 에이전트(robots.agents_allowed 에 없는 모든 토큰)에 대해 루트를 허용하지 않고, 올라 있는 공식 경로도 없어요."),
    ("The site's robots.txt disallows the root for agents it does not name (every token not in robots.agents_allowed).",
     "사이트의 robots.txt 가 이름을 정하지 않은 에이전트(robots.agents_allowed 에 없는 모든 토큰)에 대해 루트를 허용하지 않아요."),
    ("The site's robots.txt disallows the root for agents it does not name, and no official path is listed.",
     "사이트의 robots.txt 가 이름을 정하지 않은 에이전트에 대해 루트를 허용하지 않고, 올라 있는 공식 경로도 없어요."),
    ("The site's robots.txt disallows the root for agents it does not name.",
     "사이트의 robots.txt 가 이름을 정하지 않은 에이전트에 대해 루트를 허용하지 않아요."),
    ("The site's robots.txt does not block the agent tokens checked for you at the root.",
     "사이트의 robots.txt 는 루트에서 당신에게 해당하는 것으로 확인한 에이전트 토큰을 막지 않아요."),
    ("The site's robots.txt explicitly blocks agent user agents at the site root, and no official path is listed.",
     "사이트의 robots.txt 가 사이트 루트에서 에이전트 user agent 를 명시적으로 막고, 올라 있는 공식 경로도 없어요."),
    ("The site's robots.txt is being read now.", "사이트의 robots.txt 를 지금 읽고 있어요."),
    ("This is a reserved example domain (RFC 2606) for documentation; it runs no service with a status to check.",
     "문서 예시용으로 예약된 도메인(RFC 2606)이에요. 상태를 확인할 서비스가 돌고 있지 않아요."),
    ("This site publishes no robots.txt, so it declares no restriction for agents.",
     "사이트가 robots.txt 를 공개하지 않아서 에이전트에 대한 제한을 선언하지 않았어요."),
    ("This site publishes no robots.txt, so nothing declared blocks agents.",
     "사이트가 robots.txt 를 공개하지 않아서 에이전트를 막는다고 선언된 것이 없어요."),
    ("This site publishes no robots.txt.", "사이트가 robots.txt 를 공개하지 않아요."),
    ("This site publishes no robots.txt: no declared restriction for agents.",
     "사이트가 robots.txt 를 공개하지 않아요. 에이전트에 대한 제한 선언이 없어요."),
    ("This site publishes no robots.txt: no declared restriction.", "사이트가 robots.txt 를 공개하지 않아요. 선언된 제한이 없어요."),
    ("This site's status page needs your user's sign-in, so AHN cannot read it.",
     "이 사이트의 상태 페이지는 사용자의 로그인이 필요해서 AHN 이 읽을 수 없어요."),
    ("This step likely needs the user's sign-in or permission.", "이 단계에는 사용자의 로그인이나 허락이 필요해 보여요."),
    ("This step needs human verification.", "이 단계에는 사람의 확인이 필요해요."),
    ("To check if it is up, hand next_action.status_url to your user; next_action.do and url are for any other step on this site.",
     "열려 있는지 확인하려면 next_action.status_url 을 사용자에게 넘기세요. next_action.do 와 url 은 이 사이트의 다른 단계를 위한 거예요."),
    ("Treat its agent policy as unknown, not as permission.", "에이전트 정책은 허용이 아니라 알 수 없는 것으로 보세요."),
    ("Treat its policy as unknown for now; one more lookup in {n} s may help.",
     "지금은 정책을 알 수 없는 것으로 보세요. {n}초 뒤에 한 번 더 조회하면 도움이 될 수 있어요."),
    ("Use the listed official developer path instead of page automation.", "페이지 자동화 대신 올라 있는 공식 개발자 경로를 쓰세요."),
    ("Use the listed official path, not pages.", "페이지가 아니라 올라 있는 공식 경로를 쓰세요."),
    ("Use the listed official path.", "올라 있는 공식 경로를 쓰세요."),
    ("Use the official path.", "공식 경로를 쓰세요."),
    ("Wait for it to end instead of retrying in a loop, and look up again in about {m} minutes.",
     "반복해서 재시도하지 말고 끝나기를 기다리세요. 약 {m}분 뒤에 다시 조회하세요."),
    ("Wait instead of retrying in a loop; look up again in about {m} minutes.",
     "반복해서 재시도하지 말고 기다리세요. 약 {m}분 뒤에 다시 조회하세요."),
    ("Wait instead of retrying, and look up again in about {m} minutes; its status page lists the affected services.",
     "재시도하지 말고 기다리세요. 약 {m}분 뒤에 다시 조회하세요. 영향받는 서비스는 상태 페이지에 나와 있어요."),
    ("Wait instead of retrying; look up again in about {m} minutes.", "재시도하지 말고 기다리세요. 약 {m}분 뒤에 다시 조회하세요."),
    ("robots.txt blocks no listed agent vendor at the root.", "robots.txt 는 루트에서 올라 있는 어떤 에이전트 공급자도 막지 않아요."),
    ("robots.txt blocks no listed agent vendor at the site root.", "robots.txt 는 사이트 루트에서 올라 있는 어떤 에이전트 공급자도 막지 않아요."),
    ("robots.txt blocks other agents, not your vendor's.", "robots.txt 는 다른 에이전트를 막지만 당신 공급자의 에이전트는 막지 않아요."),
    ("robots.txt blocks some agent tokens (next_action.stop_if_you_are).", "robots.txt 가 일부 에이전트 토큰을 막아요(next_action.stop_if_you_are)."),
    ("robots.txt blocks some agent tokens, and this applies to requests that send them (next_action.stop_if_you_are).",
     "robots.txt 가 일부 에이전트 토큰을 막고, 그 토큰을 보내는 요청에 적용돼요(next_action.stop_if_you_are)."),
    ("robots.txt blocks some agent tokens, but none this answer checks for a listed vendor.",
     "robots.txt 가 일부 에이전트 토큰을 막지만, 이 답이 올라 있는 공급자에 대해 확인하는 토큰은 아니에요."),
    ("robots.txt blocks some agent tokens, none a listed vendor's.", "robots.txt 가 일부 에이전트 토큰을 막지만, 올라 있는 공급자의 것은 아니에요."),
    ("robots.txt blocks some agent tokens, none checked for a listed vendor.",
     "robots.txt 가 일부 에이전트 토큰을 막지만, 올라 있는 공급자에 대해 확인한 것은 아니에요."),
    ("robots.txt blocks some agent tokens; this applies to requests that send them (next_action.stop_if_you_are).",
     "robots.txt 가 일부 에이전트 토큰을 막고, 그 토큰을 보내는 요청에 적용돼요(next_action.stop_if_you_are)."),
    ("robots.txt blocks some agents, none a listed vendor's; stop if yours is in robots.agents_blocked.",
     "robots.txt 가 일부 에이전트를 막지만 올라 있는 공급자의 것은 아니에요. 내 에이전트가 robots.agents_blocked 에 있다면 멈추세요."),
    ("robots.txt blocks some agents, none checked for a vendor.", "robots.txt 가 일부 에이전트를 막지만, 공급자에 대해 확인한 것은 아니에요."),
    ("robots.txt blocks some other agents, not your vendor's tokens, at the site root.",
     "robots.txt 는 사이트 루트에서 다른 에이전트를 막지만 당신 공급자의 토큰은 막지 않아요."),
    ("robots.txt blocks some other agents, not your vendor's tokens.", "robots.txt 는 다른 에이전트를 막지만 당신 공급자의 토큰은 막지 않아요."),
    ("robots.txt blocks some tokens, including those in next_action.stop_if_you_are; if your requests send one, stop.",
     "robots.txt 가 next_action.stop_if_you_are 의 토큰을 포함한 일부 토큰을 막아요. 요청이 그중 하나를 보낸다면 멈추세요."),
    ("robots.txt blocks some tokens; stop if yours is in robots.agents_blocked.",
     "robots.txt 가 일부 토큰을 막아요. 내 에이전트가 robots.agents_blocked 에 있다면 멈추세요."),
    ("robots.txt blocks the tokens in next_action.stop_if_you_are, for requests that send them.",
     "robots.txt 가 next_action.stop_if_you_are 의 토큰을 막고, 그 토큰을 보내는 요청에 적용돼요."),
    ("robots.txt blocks your vendor's agent under another name; not permission.",
     "robots.txt 가 당신 공급자의 에이전트를 다른 이름으로 막아요. 허락이 아니에요."),
    ("robots.txt disallows the root for agents it does not name; not permission.",
     "robots.txt 가 이름을 정하지 않은 에이전트에 대해 루트를 허용하지 않아요. 허락이 아니에요."),
    ("robots.txt disallows the root for tokens not in robots.agents_allowed; not permission.",
     "robots.txt 가 robots.agents_allowed 에 없는 토큰에 대해 루트를 허용하지 않아요. 허락이 아니에요."),
    ("robots.txt does not block any listed agent vendor at the site root.", "robots.txt 는 사이트 루트에서 올라 있는 어떤 에이전트 공급자도 막지 않아요."),
    ("robots.txt does not block your vendor's agent tokens at the root.", "robots.txt 는 루트에서 당신 공급자의 에이전트 토큰을 막지 않아요."),
    ("robots.txt does not block your vendor's agent tokens at the site root.", "robots.txt 는 사이트 루트에서 당신 공급자의 에이전트 토큰을 막지 않아요."),
    ("robots.txt does not block your vendor's agents at the root.", "robots.txt 는 루트에서 당신 공급자의 에이전트를 막지 않아요."),
    ("robots.txt does not block your vendor's agents at the site root.", "robots.txt 는 사이트 루트에서 당신 공급자의 에이전트를 막지 않아요."),
    ("robots.txt is pending: look up once more in {n} s.", "robots.txt 읽기가 대기 중이에요. {n}초 뒤에 한 번 더 조회하세요."),
)
SUMMARY_SENTENCES_KO: dict[str, str] = {en: ko for en, ko in _SUMMARY_PAIRS if "{" not in en}
_SUMMARY_NUMBERED: tuple = tuple(
    (re.compile("^" + re.escape(before) + r"(\d+)" + re.escape(after) + "$"), ko, token)
    for en, ko in _SUMMARY_PAIRS if "{" in en
    for before, token, after in [re.split(r"(\{[nm]\})", en)])
_SENTENCE_SPLIT = re.compile(r"(?<=\.)\s+")
SUMMARY_KO_MAX = 401  # the length limit of server.summary_line


def summary_line_ko(line: str) -> str | None:
    """The Korean version of a lookup summary_line, or None. A line is translated only when every sentence in it has a Korean version; a line
    that would not fit server.summary_line once translated stays English too (the caller marks it)."""
    out = []
    for sentence in _SENTENCE_SPLIT.split(line.strip()):
        found = SUMMARY_SENTENCES_KO.get(sentence)
        if found is None:
            for pattern, korean, token in _SUMMARY_NUMBERED:
                match = pattern.match(sentence)
                if match:
                    found = korean.replace(token, match.group(1))
                    break
        if found is None:
            return None
        out.append(found)
    joined = " ".join(out)
    return joined if joined and len(joined) <= SUMMARY_KO_MAX else None
# The feed note (next_action.status_url_note) has its Korean version here.
STATUS_URL_NOTE_KO = {
    "The page may show a challenge, but this feed address usually answers; fetch it once.":
        "페이지는 챌린지를 보여 줄 수 있지만, 이 피드 주소는 보통 응답해요. 한 번 가져와 보세요.",
}

# D114: Korean versions of the lookup's own next_checks sentences (triage.CHECK_TEXT_KO, matched on the exact English text) and of the cause
# line and note of the server block. A cause line is made of fixed sentences filled with a window, an age and a refusal kind, so the table is per
# line template: "{win}" stands for the window ("" or " (since 11-01 03:04 UTC)", leading space included), "{age}" for the age ("just now",
# "5 min ago", "2 h ago", "3 days ago"), "{art}" for the article before the refusal kind (left out in Korean) and "{what}" for the refusal kind
# with the bot-defense provider's name when it has one. A line is translated only when it matches a template (cause_line_ko); otherwise it stays
# English and the answer says so (S-G-SERVER-EN-PARTS). A test builds every line cause.py can say, so a new line needs its Korean here.
CAUSE_NOTE = "Information only; follow next_action."
CAUSE_NOTE_KO = "참고용 정보예요. next_action 을 따르세요."
_CAUSE_PAIRS = (
    ("Temporary: provider status page shows a problem{win}. Retry later.", "일시적이에요: 업체 상태 페이지에 문제가 올라 있어요{win}. 나중에 다시 시도하세요."),
    ("Temporary: provider maintenance{win}. Retry after it ends.", "일시적이에요: 업체가 점검 중이에요{win}. 끝난 뒤에 다시 시도하세요."),
    ("Temporary: provider problem{win} has cleared. Retry.", "일시적이었어요: 업체 문제가 풀렸어요{win}. 다시 시도하세요."),
    ("AHN's own checks have not reached the site{win}; that can be AHN's network path alone, so it does not tell whether your tool can reach the site.",
     "AHN 자체 확인이 사이트에 닿지 못했어요{win}. AHN 쪽 네트워크 경로만의 문제일 수 있어서, 당신 도구가 사이트에 닿는지는 알려 주지 않아요."),
    ("AHN's own checks have not reached the site for over 7 days{win}; so long a failure is more likely AHN's network path and says nothing about the site or your tool's access.",
     "AHN 자체 확인이 7일 넘게 사이트에 닿지 못했어요{win}. 이렇게 오래된 실패는 AHN 쪽 네트워크 경로 문제일 가능성이 더 크고, 사이트의 상태나 당신 도구가 닿는지는 알려 주지 않아요."),
    ("AHN's own checks did not reach the site{win}; they answer again. That can be AHN's network path alone.",
     "AHN 자체 확인이 사이트에 닿지 못했지만{win}, 지금은 다시 응답해요. AHN 쪽 네트워크 경로만의 문제였을 수 있어요."),
    ("Probably temporary: several clients were rate limited this hour. Wait, then retry.",
     "일시적일 가능성이 커요: 이번 시간에 여러 클라이언트가 요청 한도에 걸렸어요. 기다린 뒤 다시 시도하세요."),
    ("Probably temporary: other clients saw service errors this hour. Retry later.",
     "일시적일 가능성이 커요: 이번 시간에 다른 클라이언트도 서비스 오류를 봤어요. 나중에 다시 시도하세요."),
    ("Likely a policy block: the site refused AHN's last two home-page checks.", "정책 차단일 가능성이 커요: 사이트가 AHN 의 최근 홈 페이지 확인 두 번을 거절했어요."),
    ("Likely a sign-in requirement: the site's home page showed a sign-in wall on AHN's last two checks.",
     "로그인 요구일 가능성이 커요: 사이트 홈 페이지가 AHN 의 최근 확인 두 번 모두 로그인 벽을 보여 줬어요."),
    ("Likely a policy block: the site refused AHN's home-page checks at least twice in a row; the latest showed a sign-in wall.",
     "정책 차단일 가능성이 커요: 사이트가 AHN 의 홈 페이지 확인을 연속으로 두 번 이상 거절했고, 가장 최근에는 로그인 벽을 보여 줬어요."),
    ("Likely a policy block: clients were refused on 3 or more separate days.", "정책 차단일 가능성이 커요: 클라이언트가 서로 다른 3일 이상 거절당했어요."),
    ("Likely a policy block: AHN's read of the site's robots.txt shows it now disallows this kind of agent{win}.",
     "정책 차단일 가능성이 커요: AHN 이 읽은 사이트의 robots.txt 가 이제 이런 종류의 에이전트를 허용하지 않아요{win}."),
    ("Terms signal changed{win}: llms.txt removed or data-mining reserved. Not an access block.",
     "이용 조건 신호가 바뀌었어요{win}: llms.txt 가 없어졌거나 데이터 마이닝이 유보됐어요. 접근 차단은 아니에요."),
    ("Likely a policy block: AHN's home-page check sees a bot-defense page from a new provider{win}; what your tool sees may differ.",
     "정책 차단일 가능성이 커요: AHN 의 홈 페이지 확인에 새 업체의 봇 방어 화면이 보여요{win}. 당신 도구에 보이는 것은 다를 수 있어요."),
    ("Not determined: AHN's home-page checks that day were normal, but they do not see single pages; the cause may be on your side.",
     "판단하지 못했어요: 그날 AHN 의 홈 페이지 확인은 정상이었지만 개별 페이지는 보지 못해서, 원인이 당신 쪽에 있을 수 있어요."),
    ("Not determined: AHN's home-page check {age} was normal, but it does not see single pages; the cause may be on your side.",
     "판단하지 못했어요: AHN 이 {age} 한 홈 페이지 확인은 정상이었지만 개별 페이지는 보지 못해서, 원인이 당신 쪽에 있을 수 있어요."),
    ("Not determined: AHN has no usable home-page check of this site near that time.", "판단하지 못했어요: 그 시각 즈음에 쓸 만한 AHN 의 홈 페이지 확인이 이 사이트에는 없어요."),
    ("Not determined: AHN does not measure this site because its robots.txt disallows AHN's check.",
     "판단하지 못했어요: 이 사이트의 robots.txt 가 AHN 의 확인을 허용하지 않아서 AHN 은 이 사이트를 측정하지 않아요."),
    ("Not determined: AHN does not measure this site because the site refused AHN's request for robots.txt.",
     "판단하지 못했어요: 사이트가 AHN 의 robots.txt 요청을 거절해서 AHN 은 이 사이트를 측정하지 않아요."),
    ("Not determined: that time is past the 14 days of measurements kept; only provider status history was read.",
     "판단하지 못했어요: 그 시각은 보관하는 측정 14일을 지났어요. 업체 상태 기록만 읽었어요."),
    ("Not determined: AHN's check {age} saw {art}{what} refusal; recheck_after is AHN's next check, not an instruction.",
     "판단하지 못했어요: AHN 이 {age} 한 확인에서 {what} 거절을 봤어요. recheck_after 는 AHN 의 다음 확인 시각이고 지시가 아니에요."),
    ("Not determined: AHN's check {age} saw {art}{what} refusal; AHN checks again. That is AHN's check, not an instruction.",
     "판단하지 못했어요: AHN 이 {age} 한 확인에서 {what} 거절을 봤어요. AHN 이 다시 확인해요. 이것은 AHN 의 확인이고 지시가 아니에요."),
)
_CAUSE_SLOTS = {
    "win": r"(?P<win>(?: \([^()]*\))?)",
    "age": r"(?P<age>just now|\d+ min ago|\d+ h ago|\d+ days? ago)",
    "art": r"(?:an? )",
    "what": r"(?P<what>(?:(?:" + "|".join(re.escape(name) for key, name in VENDOR_NAMES.items() if key not in UNNAMED_VENDORS) + r") )?(?:bot-challenge|HTTP|login-wall))",
}
_REFUSAL_KO = {"bot-challenge": "봇 챌린지", "HTTP": "HTTP", "login-wall": "로그인 벽"}


def _cause_regex(english: str):
    parts = re.split(r"(\{\w+\})", english)
    return re.compile("^" + "".join(_CAUSE_SLOTS[p[1:-1]] if p.startswith("{") else re.escape(p) for p in parts) + "$")


_CAUSE_COMPILED = tuple((_cause_regex(en), ko) for en, ko in _CAUSE_PAIRS)


def _window_ko(text: str) -> str:
    if not text:
        return ""
    inner = text.strip()[1:-1]
    if inner.startswith("since "):
        return f" ({inner[6:]}부터)"
    return " (" + inner.replace(" to ", " ~ ") + ")"


def _age_ko(text: str) -> str:
    if text == "just now":
        return "방금"
    number = re.match(r"\d+", text).group()
    return number + ("분" if " min " in text else "시간" if " h " in text else "일") + " 전"


def cause_line_ko(line: str) -> str | None:
    """The Korean version of a cause.line, or None when it matches no template (the caller then keeps the English line and marks it)."""
    for pattern, korean in _CAUSE_COMPILED:
        match = pattern.match(line)
        if not match:
            continue
        found = match.groupdict()
        out = korean
        if "win" in found:
            out = out.replace("{win}", _window_ko(found["win"]))
        if "age" in found:
            out = out.replace("{age}", _age_ko(found["age"]))
        if "what" in found:
            *name, kind = found["what"].split(" ")
            out = out.replace("{what}", (" ".join(name) + " " if name else "") + _REFUSAL_KO[kind])
        return out
    return None


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
# D109: what a reporting agent said it established (observations.cause), in fixed words. It always follows CAUSE_LEAD, so the
# row reads "...; the reporting agent found: ...", and the row's head says the reports are not verified by AHN. No sentence here
# names a product or says what the site does for anyone else.
CAUSE_LEAD = {"en": "; the reporting agent found: ", "ko": "; 올린 에이전트가 찾은 원인: "}
CAUSE_WORDS = {
    "site_said_no_agents": {"en": "the site said it does not take agents or automation", "ko": "사이트가 에이전트나 자동화를 받지 않는다고 했어요"},
    "site_bot_check": {"en": "a human-verification or bot-defense screen", "ko": "사람 확인이나 봇 방어 화면이 떴어요"},
    "site_unavailable": {"en": "the site was down or under maintenance", "ko": "사이트가 장애이거나 점검 중이었어요"},
    "platform_refused": {"en": "the agent's own product refused that site or action", "ko": "에이전트 자신의 제품이 그 사이트나 행동을 거절했어요"},
    "platform_needs_person": {"en": "the agent's own product rules say a person does the step", "ko": "에이전트 자신의 제품 규칙상 사람이 하는 단계였어요"},
    "verification_on_person_device": {"en": "the site asked for a code or app approval on the person's device", "ko": "사이트가 사람 기기로 코드나 앱 승인을 요구했어요"},
    "verification_not_received": {"en": "the approval request did not reach the person's device", "ko": "승인 요청이 사람 기기에 오지 않았어요"},
    "signed_out": {"en": "the session had expired or was signed out", "ko": "세션이 만료됐거나 로그아웃된 상태였어요"},
    "agent_browser_differs": {"en": "the same step works in the person's own browser or app but not in the agent's browser", "ko": "같은 단계가 사람의 자기 브라우저나 앱에서는 되고 에이전트 브라우저에서는 안 됐어요"},
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
    ("site", "change_recorded"): {"en": "AHN recorded a change in the site's declared policy or in what its own home-page check got", "ko": "AHN 이 사이트의 선언 정책이나 자신의 홈 페이지 확인 결과에서 변화를 기록했어요"},
    ("platform", "stopped_before_site"): {"en": "the agent product stopped before opening the site, and its own documentation describes sites it does not open", "ko": "에이전트 제품이 사이트를 열기 전에 멈췄고, 제품 공식 문서가 열지 않는 사이트를 설명해요"},
    ("platform", "stopped_plain"): {"en": "the agent product stopped before opening the site", "ko": "에이전트 제품이 사이트를 열기 전에 멈췄어요"},
    ("platform", "tools_off_plain"): {"en": "the page opened but the agent product's tools did not work on it", "ko": "페이지는 열렸지만 에이전트 제품의 도구가 거기서 작동하지 않았어요"},
    ("platform", "message_documented"): {"en": "the message you chose is one the product's documentation describes", "ko": "고른 메시지는 제품 문서가 설명하는 문구예요"},
    ("platform", "tools_off"): {"en": "the page opened but the agent product's tools did not work on it, and its documentation describes how a site can be limited", "ko": "페이지는 열렸지만 에이전트 제품의 도구가 거기서 작동하지 않았고, 제품 문서가 사이트 제한 방식을 설명해요"},
    ("platform", "differs_from_browser"): {"en": "the same step works in your usual browser but not in the agent product", "ko": "같은 단계가 보통 브라우저에서는 되는데 에이전트 제품에서는 안 돼요"},
    ("platform", "stopped_unnamed"): {"en": "the step stopped before the site opened; if you do not know which tool stopped it, start with that tool's own policy and settings", "ko": "사이트가 열리기 전에 멈췄어요. 어느 도구인지 모르면 그 도구의 정책·설정부터 보세요"},
    ("platform", "tools_off_unnamed"): {"en": "the page shows but the agent's tools could not act on it; if you do not know which tool, start with that tool's own policy and settings", "ko": "페이지는 보이는데 에이전트 도구가 동작하지 못했어요. 어느 도구인지 모르면 그 도구의 정책·설정부터 보세요"},
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
    "page_visible_tools_off": {"en": "the page shows but the agent's tools could not act on it, so you can open it and do the step yourself", "ko": "페이지는 보이는데 에이전트 도구가 동작하지 못해서, 직접 열어 그 단계를 하시면 돼요"},
    "check_needs_person": {"en": "one of the checks is one only you can do", "ko": "확인 하나는 직접 하셔야 해요"},
}
PERSON_REASON_ID = {
    "verification_on_person_device": "S-P-VERIFY-DEVICE", "sign_in_is_the_account_holders": "S-P-SIGN-IN",
    "approval_is_the_persons_decision": "S-P-APPROVAL", "company_it_decides": "S-P-COMPANY-IT",
    "platform_admin_decides": "S-P-PLATFORM-ADMIN", "platform_refused_step": "S-P-PLATFORM-REFUSED",
    "server_hand_off": "S-P-SERVER-HAND-OFF", "page_visible_tools_off": "S-P-TOOLS-OFF", "check_needs_person": "S-P-CHECK-PERSON",
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
                        ("ENV_WORDS", ENV_WORDS), ("CAUSE_LEAD", CAUSE_LEAD), ("CAUSE_WORDS", CAUSE_WORDS), ("LAYER_WORDS", LAYER_WORDS), ("REASON_SHORT", REASON_SHORT),
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
