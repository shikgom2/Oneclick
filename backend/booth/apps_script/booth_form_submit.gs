/**
 * 부스 체험 설문(구글 폼) 제출을 Oneclick 서버 /booth/api/survey/ 로 전달하는 Apps Script.
 *
 * 폼에 연결된(form-bound) 스크립트다. 폼 편집 화면의 점 3개 메뉴 > '스크립트 편집기'로 만든
 * 프로젝트에 이 파일 내용을 그대로 붙여 넣는다. 전체 절차는 backend/booth/README.md 참고.
 *
 * 스크립트 속성 (프로젝트 설정 > 스크립트 속성)
 *   BOOTH_BASE_URL        서버 주소. https:// 만 받는다. 예: https://180.83.245.145  (끝의 '/' 는 있어도 된다)
 *                         이름·건강 설문 응답을 보내므로 평문 http 주소(:8000 등)는 설정 오류로 막는다.
 *   BOOTH_FORM_API_KEY    서버 env BOOTH_FORM_API_KEY 와 같은 값(설문 수신 전용 키).
 *                         (예전 속성 이름 BOOTH_API_KEY 도 읽는다.)
 *   BOOTH_NUMBER_TITLE    (선택) 참가자 번호 문항 제목. 기본 '참가자 번호'. 서버 BOOTH_SURVEY_NUMBER_TITLE 과 같게.
 *   BOOTH_CODE_TITLE      (선택) 확인 코드 문항 제목. 기본 '확인 코드'. 서버 BOOTH_SURVEY_CODE_TITLE 과 같게.
 *   BOOTH_CONSENT_TITLES  (선택) 동의 문항 제목들을 '|' 로 이은 값. 기본 '개인정보 수집·이용 동의'.
 *                         서버 BOOTH_SURVEY_CONSENT_TITLES 와 같게.
 *   BOOTH_RETENTION_DAYS  (선택) 폼 응답 보유기간(일). 기본 14. 1 이상의 정수만 받고, 그 밖의 값이면
 *                         경고를 남기고 14 를 쓴다. 서버 BOOTH_RETENTION_DAYS 와 같게.
 *   키를 코드에 적지 않는 이유: 이 파일은 git 저장소에 들어가고 여러 사람에게 공유된다.
 *
 * 처음 한 번: 속성을 넣고 편집기에서 installTrigger 를 실행해 권한을 승인한다.
 * 점검:       checkConnection 을 실행하면 주소·키가 맞는지 실행 로그에 나온다.
 *             previewExpiredResponses 를 실행하면 지금 삭제 대상인 응답 건수가 나온다(삭제하지 않는다).
 *
 * 폼 응답 자동 삭제: installTrigger 가 매일 새벽 4~5시(프로젝트 설정의 시간대) deleteExpiredResponses 트리거를
 * 설치한다. 제출 시각이 '지금 - 보유기간' 보다 이전인 응답만 오래된 순서로 지우므로, 제출 후 14일이
 * 지난 응답은 그 뒤 첫 실행(최대 약 하루 뒤)에 지워진다. 한 번에 최대 500건·4분까지 지우고(실행 시간
 * 6분 제한), 남은 건은 다음 실행이 이어서 지운다. 삭제는 되돌릴 수 없다. 연결된 스프레드시트의 행은
 * 지우지 않으므로 스프레드시트는 연결하지 않는다.
 *
 * 동의 거부: 동의 문항이 있는데 하나라도 거부했으면 이름·설문 응답은 보내지 않고 번호·확인 코드·
 * 동의 문항만 보낸다(서버도 거부자의 응답은 저장하지 않는다). 동의 문항을 찾지 못하면 판단하지 않고
 * 그대로 보낸다. 서버가 최종 판단하고, 동의가 확인되지 않으면 버린다.
 *
 * 전송 실패 처리
 *   - 네트워크 오류·5xx·429 는 2초, 4초, 8초 간격으로 3번 더 시도한다.
 *   - 그래도 실패하거나 키·주소 문제(403, 3xx, booth 형식이 아닌 4xx)면 응답 ID 만 재전송 대기열에 넣고,
 *     10분마다 도는 retryFailedSubmissions 가 다시 보낸다. 대기열에는 응답 ID 와 시각만 저장한다
 *     (응답 내용은 폼에 이미 있으므로 복사하지 않는다).
 *   - 서버가 {"error": ...} 로 답한 400·404·409(번호·확인 코드 오류 등)는 다시 보내도 결과가 같으므로
 *     재전송하지 않는다. HTML 404·405(주소 오류, 서버 배포 누락)는 설정 문제로 보고 대기열에 넣는다.
 *   실패한 실행은 '실행' 목록에 '실패'로 표시되도록 마지막에 오류를 던진다.
 *
 * 개인정보: 실행 로그는 편집 권한이 있는 사람이 모두 본다. 로그에는 참가자 번호와 HTTP
 * 상태만 남기고 이름·응답 내용·API 키는 절대 쓰지 않는다. 자동 삭제 로그에는 건수와 시각만 남긴다.
 *
 * 런타임: V8 (새 Apps Script 프로젝트의 기본값). 같은 프로젝트의 다른 파일과 전역 이름이
 * 겹치지 않도록 상수는 BOOTH_ 접두어, 내부 함수는 '_' 로 끝나는 이름을 쓴다.
 */

const BOOTH_SUBMIT_HANDLER = 'onBoothFormSubmit';
const BOOTH_RETRY_HANDLER = 'retryFailedSubmissions';
const BOOTH_DELETE_HANDLER = 'deleteExpiredResponses';
const BOOTH_DEFAULT_NUMBER_TITLE = '참가자 번호';
const BOOTH_DEFAULT_CODE_TITLE = '확인 코드';
const BOOTH_DEFAULT_CONSENT_TITLES = '개인정보 수집·이용 동의';
const BOOTH_SURVEY_PATH = '/booth/api/survey/';   // Django URL 이 '/' 로 끝나야 POST 가 리다이렉트되지 않는다

const BOOTH_MAX_RETRIES = 3;            // 첫 시도 뒤 재시도 횟수 (총 4번)
const BOOTH_BACKOFF_BASE_MS = 2000;     // 2초, 4초, 8초
const BOOTH_RETRY_EVERY_MINUTES = 10;   // Apps Script 가 허용하는 값: 1, 5, 10, 15, 30
const BOOTH_RETRY_BATCH = 20;           // 한 번에 재전송할 최대 건수 (실행 시간 6분 제한)
const BOOTH_QUEUE_PROPERTY = 'BOOTH_FAILED_QUEUE';
const BOOTH_QUEUE_MAX_ITEMS = 60;       // 속성 값 하나의 한도(9KB) 안에 들어가는 개수
// 태블릿 대기 목록(서버 BOOTH_PENDING_WINDOW_HOURS 기본 12시간)에서 빠질 만큼 지난 건은
// 체험자가 이미 떠났으므로 더 보내지 않는다.
const BOOTH_QUEUE_MAX_AGE_MS = 12 * 60 * 60 * 1000;
// 서버(services._CONSENT_NEGATIVES)와 같은 부정 표현
const BOOTH_CONSENT_NEGATIVES = ['동의하지', '미동의', '비동의', '거부', '동의안', '않'];

// 폼 응답 자동 삭제 (deleteExpiredResponses)
const BOOTH_RETENTION_PROPERTY = 'BOOTH_RETENTION_DAYS';
const BOOTH_DEFAULT_RETENTION_DAYS = 14;          // 서버 BOOTH_RETENTION_DAYS 기본값과 같다
const BOOTH_DAY_MS = 24 * 60 * 60 * 1000;
const BOOTH_DELETE_AT_HOUR = 4;                   // 매일 새벽 4~5시 사이 (프로젝트 설정의 시간대)
const BOOTH_DELETE_BATCH = 500;                   // 한 번 실행에서 삭제를 시도할 최대 건수
const BOOTH_DELETE_TIME_BUDGET_MS = 4 * 60 * 1000;  // 실행 시간 6분 제한보다 넉넉히 먼저 멈춘다


// ---------------------------------------------------------------------------
// 편집기에서 직접 실행하는 함수
// ---------------------------------------------------------------------------

/**
 * 제출 트리거, 재전송 트리거, 폼 응답 자동 삭제 트리거를 설치한다. 여러 번 실행해도 중복되지 않는다.
 */
function installTrigger() {
  const form = activeFormOrThrow_();
  requireConfig_();   // 속성이 빠졌으면 설치 단계에서 바로 알린다
  const retentionDays = retentionDays_();   // 값이 잘못됐으면 설치 로그에 경고가 먼저 나온다

  // 제출 트리거가 두 개 생기면 제출 한 번에 요청이 두 번 간다(서버는 같은 제출을 한 번만 반영하지만
  // 실행 할당량을 두 배로 쓴다). 기존 트리거를 지우고 새로 만든다.
  let replaced = 0;
  ScriptApp.getProjectTriggers().forEach(function (trigger) {
    const handler = trigger.getHandlerFunction();
    if (handler === BOOTH_SUBMIT_HANDLER || handler === BOOTH_RETRY_HANDLER || handler === BOOTH_DELETE_HANDLER) {
      ScriptApp.deleteTrigger(trigger);
      replaced += 1;
    }
  });
  ScriptApp.newTrigger(BOOTH_SUBMIT_HANDLER).forForm(form).onFormSubmit().create();
  ScriptApp.newTrigger(BOOTH_RETRY_HANDLER).timeBased().everyMinutes(BOOTH_RETRY_EVERY_MINUTES).create();
  ScriptApp.newTrigger(BOOTH_DELETE_HANDLER).timeBased().everyDays(1).atHour(BOOTH_DELETE_AT_HOUR).create();
  console.info('[booth] 트리거 설치 완료 (기존 ' + replaced + '개 교체): 제출 시 전송, '
    + BOOTH_RETRY_EVERY_MINUTES + '분마다 실패 건 재전송, 매일 ' + BOOTH_DELETE_AT_HOUR + '~'
    + (BOOTH_DELETE_AT_HOUR + 1) + '시에 제출 후 ' + retentionDays + '일 지난 폼 응답 삭제');
}

/**
 * 주소·키 점검. 빈 번호로 요청을 보내 서버의 응답 코드로 판단한다.
 * 서버는 https·키를 먼저 확인하고 본문을 나중에 검사하므로, 400 이 오면 주소와 키가 맞는 것이다.
 */
function checkConnection() {
  const config = requireConfig_();
  const result = postOnce_(config, { number: '', answers: {} });
  let message;
  if (result.status === 400 && result.boothJson) {
    message = '정상: 주소와 키가 맞습니다 (빈 번호에 대한 400 응답이 정상입니다).';
  } else if (result.status === 403 && /HTTPS/i.test(result.detail)) {
    message = 'HTTPS 필요: 서버가 https 요청으로 보지 않았습니다. BOOTH_BASE_URL 과 nginx 443 설정을 확인하세요.';
  } else if (result.status === 403) {
    message = '키 불일치: 스크립트 속성 BOOTH_FORM_API_KEY 가 서버 env BOOTH_FORM_API_KEY 와 다릅니다.';
  } else if (result.status === 503) {
    message = '서버에 API 키가 설정되지 않았습니다 (backend/.env 설정 후 uWSGI 재시작 필요).';
  } else if (result.status >= 300 && result.status < 400) {
    message = '리다이렉트(HTTP ' + result.status + '): BOOTH_BASE_URL 주소를 확인하세요.';
  } else if (result.status === 0) {
    message = '서버에 연결하지 못했습니다: ' + result.detail;
  } else {
    message = '예상하지 못한 응답 HTTP ' + result.status + (result.boothJson ? '' : ' (booth 형식 아님)')
      + ': BOOTH_BASE_URL 과 서버 배포 상태(urls.py 의 booth 줄)를 확인하세요. 이 상태의 제출은 재전송 대기열에 들어갑니다.';
  }
  message += ' / 재전송 대기 ' + readQueue_().length + '건';
  console.info('[booth] 연결 점검 ' + config.surveyUrl + ' -> ' + message);
  return message;
}

/**
 * 가장 최근 응답 1건을 다시 보낸다. 설정을 고친 뒤 대기열에 없는 건을 보낼 때 쓴다.
 * 서버는 이미 받은 같은 제출이면 아무것도 바꾸지 않는다.
 */
function resendLatestResponse() {
  const config = requireConfig_();
  const responses = FormApp.getActiveForm().getResponses();
  if (!responses.length) {
    console.info('[booth] 보낼 응답이 없습니다.');
    return;
  }
  const latest = responses.reduce(function (a, b) {
    return b.getTimestamp().getTime() > a.getTimestamp().getTime() ? b : a;
  });
  const payload = buildPayload_(latest, config);
  const label = safeLabel_(payload.number);
  const result = sendWithRetry_(config, payload, label);
  console.info('[booth] 최근 응답 재전송 ' + label + ': '
    + (result.outcome === 'ok' ? '성공' : '실패 HTTP ' + result.status));
}

/**
 * 자동 삭제 대상 미리 보기. deleteExpiredResponses 와 같은 기준으로 고르지만 아무것도 지우지 않는다.
 * 로그와 반환값에는 건수와 시각만 있다.
 */
function previewExpiredResponses() {
  const form = activeFormOrThrow_();
  const plan = planExpiredResponses_(form, Date.now());
  const summary = {
    retentionDays: plan.retentionDays,
    cutoff: formatTime_(plan.cutoff),
    total: plan.total,
    expired: plan.expired.length,
    nextRunLimit: Math.min(plan.expired.length, BOOTH_DELETE_BATCH),
    oldestExpired: plan.expired.length ? formatTime_(plan.expired[0].time) : null,
    oldestResponse: plan.oldestTime === null ? null : formatTime_(plan.oldestTime),
    invalid: plan.invalid,
  };
  let message = '[booth] 자동 삭제 미리 보기 (삭제하지 않음): 보유기간 ' + summary.retentionDays + '일, '
    + summary.cutoff + ' 이전 제출 ' + summary.expired + '건 / 전체 응답 ' + summary.total + '건';
  if (summary.expired) {
    message += ', 가장 오래된 삭제 대상 제출 ' + summary.oldestExpired;
    if (summary.expired > BOOTH_DELETE_BATCH) {
      message += ' (한 번 실행에 최대 ' + BOOTH_DELETE_BATCH + '건, 나머지는 다음 실행)';
    }
  } else if (summary.oldestResponse !== null) {
    message += ', 가장 오래된 응답 제출 ' + summary.oldestResponse;
  }
  if (summary.invalid) {
    message += ', 제출 시각·ID 를 읽지 못해 제외한 응답 ' + summary.invalid + '건';
  }
  console.info(message);
  return summary;
}


// ---------------------------------------------------------------------------
// 트리거가 실행하는 함수
// ---------------------------------------------------------------------------

/** 폼 제출 트리거. installTrigger 가 설치한다. */
function onBoothFormSubmit(e) {
  if (!e || !e.response) {
    console.warn('[booth] 폼 제출 이벤트 없이 실행되었습니다. 점검은 checkConnection 을 실행하세요.');
    return;
  }
  const response = e.response;
  const config = boothConfig_();
  const payload = buildPayload_(response, config);
  const label = safeLabel_(payload.number);

  if (!payload.number) {
    // 번호가 없으면 서버가 누구의 설문인지 알 수 없다. 재전송해도 같으므로 대기열에 넣지 않는다.
    throw new Error('[booth] 참가자 번호 응답이 없어 전송하지 않았습니다. 번호 문항 제목이 "'
      + config.numberTitle + '" 인지 확인하세요.');
  }
  if (config.problem) {
    enqueue_(response.getId(), response.getTimestamp().getTime());
    throw new Error('[booth] ' + config.problem + ' (' + label + ' 은 재전송 대기열에 넣었습니다)');
  }

  const result = sendWithRetry_(config, payload, label);
  if (result.outcome === 'ok') {
    return;
  }
  if (result.outcome === 'drop') {
    throw new Error('[booth] 설문 전달 실패 ' + label + ' HTTP ' + result.status
      + (result.detail ? ' ' + result.detail : '') + ' (다시 보내도 같아 재전송하지 않습니다)');
  }
  enqueue_(response.getId(), response.getTimestamp().getTime());
  throw new Error('[booth] 설문 전달 실패 ' + label + ' HTTP ' + result.status
    + ', ' + BOOTH_RETRY_EVERY_MINUTES + '분마다 자동 재전송합니다.');
}

/** 10분마다 실행되어 실패한 제출을 오래된 순서로 다시 보낸다. */
function retryFailedSubmissions() {
  const queue = readQueue_();
  if (!queue.length) {
    return;   // 평소에는 여기서 바로 끝난다 (트리거 실행 시간 할당량 절약)
  }
  const config = boothConfig_();
  if (config.problem) {
    console.warn('[booth] 재전송 보류 (' + queue.length + '건): ' + config.problem);
    return;
  }
  const form = FormApp.getActiveForm();
  const now = Date.now();
  const batch = queue.slice().sort(function (a, b) { return a.at - b.at; }).slice(0, BOOTH_RETRY_BATCH);

  for (let i = 0; i < batch.length; i++) {
    const entry = batch[i];
    if (!readQueue_().some(function (e) { return e.id === entry.id; })) {
      continue;   // 그 사이 다른 실행이 처리했다
    }
    if (now - entry.at > BOOTH_QUEUE_MAX_AGE_MS) {
      removeFromQueue_(entry.id);
      console.warn('[booth] 12시간이 지난 실패 건 1건은 재전송하지 않고 대기열에서 뺍니다.');
      continue;
    }
    const response = findResponse_(form, entry.id);
    if (!response) {
      removeFromQueue_(entry.id);
      console.warn('[booth] 폼에서 삭제된 응답 1건을 대기열에서 뺍니다.');
      continue;
    }
    const payload = buildPayload_(response, config);
    const label = safeLabel_(payload.number);
    if (!payload.number) {
      removeFromQueue_(entry.id);
      console.warn('[booth] 참가자 번호가 없는 응답 1건을 대기열에서 뺍니다.');
      continue;
    }
    // 같은 번호로 더 최근에 제출된 응답이 있으면 그것이 이미 전달됐거나 대기 중이다.
    // 오래된 응답은 서버도 무시하지만, 보내지 않아 할당량을 아낀다.
    if (hasNewerResponseForNumber_(form, response, payload.number, config)) {
      removeFromQueue_(entry.id);
      console.info('[booth] ' + label + ' 은 더 최근 응답이 있어 이전 응답을 재전송하지 않습니다.');
      continue;
    }

    const result = postOnce_(config, payload);
    if (result.outcome === 'ok') {
      removeFromQueue_(entry.id);
      console.info('[booth] 설문 재전송 성공 ' + label);
      continue;
    }
    console.warn('[booth] 설문 재전송 실패 ' + label + ' HTTP ' + result.status
      + (result.detail ? ' ' + result.detail : ''));
    if (result.outcome === 'drop') {
      removeFromQueue_(entry.id);
      continue;
    }
    break;   // 서버 장애·설정 문제면 나머지도 실패할 것이므로 다음 주기에 다시 한다
  }
}

/**
 * 매일 새벽 4~5시 트리거(installTrigger 가 설치). 제출 시각이 '지금 - 보유기간' 보다 엄격히 이전인
 * 폼 응답만 오래된 순서로 지운다. 기준 시각은 실행 시작 때 한 번 정하므로 실행 중에 기간이 찬 응답도
 * 이번에는 지우지 않는다. 한 번에 최대 BOOTH_DELETE_BATCH 건, 시작 후 4분까지만 지우고 남은 건은
 * 다음 실행(또는 편집기에서 다시 실행)이 이어서 지운다. 삭제는 되돌릴 수 없다.
 * 예상하지 못한 오류는 건수를 담아 던져 '실행' 목록에 '실패'로 남긴다.
 */
function deleteExpiredResponses() {
  const startedAt = Date.now();
  const form = activeFormOrThrow_();
  const plan = planExpiredResponses_(form, startedAt);
  const scope = '보유기간 ' + plan.retentionDays + '일, ' + formatTime_(plan.cutoff) + ' 이전 제출';

  let attempted = 0;
  let deleted = 0;
  const vanished = [];   // 삭제 호출이 실패했지만 폼에서 이미 사라진 응답 (다른 실행이 먼저 지움)
  let stopReason = '';
  for (let i = 0; i < plan.expired.length; i++) {
    if (attempted >= BOOTH_DELETE_BATCH) {
      stopReason = '한 번 실행 상한 ' + BOOTH_DELETE_BATCH + '건';
      break;
    }
    if (Date.now() - startedAt >= BOOTH_DELETE_TIME_BUDGET_MS) {
      stopReason = '실행 시간 ' + Math.round(BOOTH_DELETE_TIME_BUDGET_MS / 60000) + '분';
      break;
    }
    const entry = plan.expired[i];
    if (!(entry.time < plan.cutoff)) {
      // planExpiredResponses_ 가 이미 거른다. 기준이 어긋나면 한 건도 더 지우지 않는다.
      throw new Error('[booth] 폼 응답 자동 삭제 중단: 기준 시각 이후 응답이 대상에 섞였습니다 (삭제 '
        + deleted + '건 뒤 멈춤, ' + scope + ').');
    }
    attempted += 1;
    try {
      form.deleteResponse(entry.id);
      deleted += 1;
    } catch (err) {
      if (findResponse_(form, entry.id)) {
        throw new Error('[booth] 폼 응답 자동 삭제 실패 (삭제 ' + deleted + '건 뒤 멈춤, 남은 대상 '
          + (plan.expired.length - deleted - vanished.length) + '건, ' + scope + '): ' + errorText_(err));
      }
      vanished.push(entry.id);
    }
  }

  if (vanished.length) {
    // 응답 조회까지 일시 장애였다면 지우지 못한 응답을 '이미 사라짐'으로 셌을 수 있다. 목록을 다시 읽어 확인한다.
    const present = {};
    form.getResponses().forEach(function (response) { present[response.getId()] = true; });
    const stillThere = vanished.filter(function (id) { return present[id] === true; }).length;
    if (stillThere) {
      throw new Error('[booth] 폼 응답 자동 삭제 실패: 삭제하지 못한 응답 ' + stillThere + '건 (삭제 '
        + deleted + '건, ' + scope + ').');
    }
    console.warn('[booth] 다른 실행이 먼저 지운 응답 ' + vanished.length + '건은 건너뛰었습니다.');
  }

  const remaining = plan.expired.length - deleted - vanished.length;
  const summary = {
    retentionDays: plan.retentionDays, cutoff: formatTime_(plan.cutoff), total: plan.total,
    expired: plan.expired.length, deleted: deleted, alreadyGone: vanished.length,
    remaining: remaining, invalid: plan.invalid,
  };
  let message = '[booth] 폼 응답 자동 삭제: 삭제 ' + deleted + '건, 남은 대상 ' + remaining + '건 (' + scope
    + ', 삭제 전 전체 응답 ' + plan.total + '건)';
  if (plan.invalid) {
    message += ', 제출 시각·ID 를 읽지 못해 지우지 않은 응답 ' + plan.invalid + '건';
  }
  if (remaining > 0) {
    console.warn(message + '. ' + (stopReason || '중단') + '에 닿아 멈췄고 나머지는 다음 실행에서 이어서 지웁니다.');
  } else if (plan.invalid) {
    console.warn(message);
  } else {
    console.info(message);
  }
  return summary;
}


// ---------------------------------------------------------------------------
// 설정
// ---------------------------------------------------------------------------

function boothConfig_() {
  const props = PropertiesService.getScriptProperties();
  // 'https://host/', 'https://host/booth/' 처럼 넣어도 경로가 두 번 붙지 않게 정리한다.
  const base = String(props.getProperty('BOOTH_BASE_URL') || '').trim()
    .replace(/\/+$/, '').replace(/\/booth$/i, '').replace(/\/+$/, '');
  const apiKey = String(props.getProperty('BOOTH_FORM_API_KEY') || props.getProperty('BOOTH_API_KEY') || '').trim();
  const numberTitle = String(props.getProperty('BOOTH_NUMBER_TITLE') || '').trim() || BOOTH_DEFAULT_NUMBER_TITLE;
  const codeTitle = String(props.getProperty('BOOTH_CODE_TITLE') || '').trim() || BOOTH_DEFAULT_CODE_TITLE;
  const consentTitles = String(props.getProperty('BOOTH_CONSENT_TITLES') || BOOTH_DEFAULT_CONSENT_TITLES)
    .split('|').map(function (t) { return t.trim(); }).filter(function (t) { return t; });

  let problem = '';
  if (!base || !apiKey) {
    problem = '스크립트 속성 BOOTH_BASE_URL 과 BOOTH_FORM_API_KEY 를 설정하세요 (프로젝트 설정 > 스크립트 속성).';
  } else if (!/^https:\/\/[^\/\s]+/i.test(base)) {
    // 이름·건강 설문 응답·키를 평문으로 보내지 않는다. 서버가 거부해도 본문은 이미 전송된 뒤다.
    problem = 'BOOTH_BASE_URL 은 https:// 로 시작해야 합니다 (개인정보 암호화 전송).';
  }
  return {
    surveyUrl: base + BOOTH_SURVEY_PATH, apiKey: apiKey, numberTitle: numberTitle,
    codeTitle: codeTitle, consentTitles: consentTitles, problem: problem,
  };
}

function requireConfig_() {
  const config = boothConfig_();
  if (config.problem) {
    throw new Error('[booth] ' + config.problem);
  }
  return config;
}

function activeFormOrThrow_() {
  const form = FormApp.getActiveForm();
  if (!form) {
    throw new Error('구글 폼 편집 화면에서 연 스크립트(폼에 연결된 스크립트)에서 실행해야 합니다.');
  }
  return form;
}

/** 스크립트 속성 BOOTH_RETENTION_DAYS. 없으면 14, 1 이상의 정수가 아니면 경고 후 14 (서버 conf 와 같은 규칙). */
function retentionDays_() {
  const raw = PropertiesService.getScriptProperties().getProperty(BOOTH_RETENTION_PROPERTY);
  const text = String(raw === null || raw === undefined ? '' : raw).trim();
  if (!text) {
    return BOOTH_DEFAULT_RETENTION_DAYS;
  }
  const days = /^\d+$/.test(text) ? Number(text) : NaN;
  if (!Number.isSafeInteger(days) || days < 1) {
    console.warn('[booth] 스크립트 속성 ' + BOOTH_RETENTION_PROPERTY + ' 값이 1 이상의 정수가 아니어서 기본값 '
      + BOOTH_DEFAULT_RETENTION_DAYS + '일을 씁니다.');
    return BOOTH_DEFAULT_RETENTION_DAYS;
  }
  return days;
}


// ---------------------------------------------------------------------------
// 응답 -> 서버 본문
// ---------------------------------------------------------------------------

/** {number, answers: {문항 제목: 문자열 | 문자열 배열}, submitted_at} */
function buildPayload_(formResponse, config) {
  const answers = collectAnswers_(formResponse);
  const key = findTitle_(answers, config.numberTitle);
  let sent = answers;
  if (consentRefused_(answers, config.consentTitles)) {
    // 동의를 거부한 사람의 이름·설문 응답은 보내지 않는다. 서버가 거부 상태를 알 수 있을 만큼만 보낸다.
    sent = {};
    [config.numberTitle, config.codeTitle].concat(config.consentTitles).forEach(function (title) {
      const k = findTitle_(answers, title);
      if (k !== null) {
        sent[k] = answers[k];
      }
    });
  }
  return {
    number: key === null ? '' : firstText_(answers[key]),
    answers: sent,
    submitted_at: formResponse.getTimestamp().toISOString(),
  };
}

/** 설정한 동의 문항 중 '있는데 거부한' 것이 하나라도 있으면 true. 문항이 없으면 판단하지 않는다. */
function consentRefused_(answers, titles) {
  for (let i = 0; i < titles.length; i++) {
    const key = findTitle_(answers, titles[i]);
    if (key !== null && !parseConsent_(answers[key])) {
      return true;
    }
  }
  return false;
}

/** 서버(services.parse_consent)와 같은 규칙: '동의'가 있고 부정 표현이 없을 때만 동의. */
function parseConsent_(value) {
  const text = (Array.isArray(value) ? value.join('') : String(value === null || value === undefined ? '' : value))
    .replace(/\s+/g, '');
  if (text.indexOf('동의') < 0) {
    return false;
  }
  return !BOOTH_CONSENT_NEGATIVES.some(function (neg) { return text.indexOf(neg) >= 0; });
}

function collectAnswers_(formResponse) {
  const answers = {};
  formResponse.getItemResponses().forEach(function (itemResponse) {
    const item = itemResponse.getItem();
    const title = String(item.getTitle() || '').trim() || ('문항 ' + (item.getIndex() + 1));
    // 제목이 같은 문항이 여러 개면 뒤 응답이 앞 응답을 덮어쓰지 않게 번호를 붙인다.
    // (서버는 '이름 (2)' 처럼 번호가 붙은 신원 문항도 AI 로 보내지 않는다.)
    let unique = title;
    for (let n = 2; Object.prototype.hasOwnProperty.call(answers, unique); n++) {
      unique = title + ' (' + n + ')';
    }
    answers[unique] = answerValue_(item, itemResponse.getResponse());
  });
  return answers;
}

/**
 * 응답 값을 문자열 또는 문자열 배열로. 체크박스처럼 배열인 응답은 배열로 두고,
 * 그리드(객관식 그리드·체크박스 그리드)는 '행 제목: 선택' 문자열 배열로 펼친다.
 * 행 제목이 없으면 AI 가 '보통'이 어느 질문의 답인지 알 수 없기 때문이다.
 */
function answerValue_(item, value) {
  const type = item.getType();
  if (type === FormApp.ItemType.GRID || type === FormApp.ItemType.CHECKBOX_GRID) {
    const rows = type === FormApp.ItemType.GRID
      ? item.asGridItem().getRows()
      : item.asCheckboxGridItem().getRows();
    const lines = [];
    (value || []).forEach(function (cell, i) {
      const text = Array.isArray(cell)
        ? cell.filter(function (v) { return v !== null && v !== undefined && v !== ''; }).join(', ')
        : (cell === null || cell === undefined ? '' : String(cell));
      if (text) {
        lines.push(rows[i] !== undefined ? rows[i] + ': ' + text : text);
      }
    });
    return lines;
  }
  if (Array.isArray(value)) {
    return value
      .filter(function (v) { return v !== null && v !== undefined; })
      .map(function (v) { return Array.isArray(v) ? v.join(', ') : String(v); });
  }
  return value === null || value === undefined ? '' : String(value);
}

/** 서버(services._normalize_title)와 같은 규칙: 공백 제거, 앞뒤 '*' 제거, 소문자. */
function normalizeTitle_(title) {
  return String(title).replace(/\s+/g, '').replace(/^\*+|\*+$/g, '').toLowerCase();
}

function findTitle_(answers, title) {
  if (Object.prototype.hasOwnProperty.call(answers, title)) {
    return title;
  }
  const target = normalizeTitle_(title);
  const keys = Object.keys(answers);
  for (let i = 0; i < keys.length; i++) {
    if (normalizeTitle_(keys[i]) === target) {
      return keys[i];
    }
  }
  return null;
}

function firstText_(value) {
  if (Array.isArray(value)) {
    for (let i = 0; i < value.length; i++) {
      const text = String(value[i]).trim();
      if (text) return text;
    }
    return '';
  }
  return String(value === null || value === undefined ? '' : value).trim();
}

/** 로그용 번호. 번호 칸에 이름 등 다른 글자를 적었을 수 있어 번호 형식일 때만 그대로 쓴다. */
function safeLabel_(number) {
  const text = String(number || '').trim();
  return /^[A-Za-z]{0,10}\s*[-‐-―−]?\s*\d{1,10}$/.test(text) ? text : '(번호 형식 아님)';
}

/** 'SF-042', '42', 'sf042' 를 같은 번호로 비교하기 위한 키. */
function numberKey_(number) {
  const digits = String(number || '').replace(/\D+/g, '');
  return digits ? String(parseInt(digits, 10)) : '';
}


// ---------------------------------------------------------------------------
// 전송
// ---------------------------------------------------------------------------

/** 한 번 보낸다. {status, outcome: ok|retry|config|drop, detail, boothJson} (status 0 = 네트워크 오류) */
function postOnce_(config, payload) {
  let status = 0;
  let detail = '';
  let boothJson = false;
  try {
    const res = UrlFetchApp.fetch(config.surveyUrl, {
      method: 'post',
      contentType: 'application/json; charset=utf-8',
      headers: { 'X-Booth-Key': config.apiKey },
      payload: JSON.stringify(payload),
      muteHttpExceptions: true,    // 4xx/5xx 도 예외 대신 응답으로 받아 코드로 판단한다
      followRedirects: false,      // POST 가 리다이렉트되면 GET 으로 바뀌어 본문이 사라진다
    });
    status = res.getResponseCode();
    const parsed = parseServerError_(res.getContentText());
    boothJson = parsed.boothJson;
    detail = parsed.message;
  } catch (err) {
    // DNS·타임아웃·인증서 오류. 메시지에는 주소만 들어가고 본문은 들어가지 않는다.
    detail = String((err && err.message) || err).slice(0, 150);
  }
  return { status: status, outcome: outcomeFor_(status, boothJson), detail: detail, boothJson: boothJson };
}

function sendWithRetry_(config, payload, label) {
  const attempts = BOOTH_MAX_RETRIES + 1;
  let result = null;
  for (let attempt = 1; attempt <= attempts; attempt++) {
    if (attempt > 1) {
      Utilities.sleep(BOOTH_BACKOFF_BASE_MS * Math.pow(2, attempt - 2));
    }
    result = postOnce_(config, payload);
    if (result.outcome === 'ok') {
      console.info('[booth] 설문 전달 성공 ' + label + (attempt > 1 ? ' (' + attempt + '번째 시도)' : ''));
      return result;
    }
    console.warn('[booth] 설문 전달 실패 ' + label + ' 시도 ' + attempt + '/' + attempts
      + ': HTTP ' + result.status + (result.detail ? ' ' + result.detail : ''));
    if (result.outcome !== 'retry') {
      break;   // 키·주소·번호 문제는 곧바로 다시 보내도 결과가 같다
    }
  }
  return result;
}

function outcomeFor_(status, boothJson) {
  if (status >= 200 && status < 300) return 'ok';
  if (status === 0 || status === 408 || status === 429 || status >= 500) return 'retry';
  if (status === 401 || status === 403 || (status >= 300 && status < 400)) return 'config';
  // 나머지 4xx: booth API 가 {"error"} 로 답한 것(번호·확인 코드 오류)만 버린다. HTML 404·405 는 주소가
  // 틀렸거나 서버 배포에서 booth 경로가 빠진 것이라, 고친 뒤 다시 보낼 수 있게 대기열에 넣는다.
  return boothJson ? 'drop' : 'config';
}

/**
 * 서버 오류 응답의 {"error": "..."} 문구만 짧게 꺼낸다. booth API 오류 문구에는 개인정보가
 * 없다. HTML 오류 페이지(DEBUG 화면 등)는 요청 내용이 섞일 수 있어 로그에 쓰지 않는다.
 */
function parseServerError_(body) {
  if (!body) return { boothJson: false, message: '' };
  try {
    const parsed = JSON.parse(body);
    if (parsed && typeof parsed.error === 'string') {
      return { boothJson: true, message: parsed.error.slice(0, 100) };
    }
    return { boothJson: false, message: '' };
  } catch (err) {
    return { boothJson: false, message: '' };
  }
}


// ---------------------------------------------------------------------------
// 재전송 대기열 (스크립트 속성에 [{id: 응답 ID, at: 제출 시각 ms}] 로 저장)
// ---------------------------------------------------------------------------

function readQueue_() {
  const raw = PropertiesService.getScriptProperties().getProperty(BOOTH_QUEUE_PROPERTY);
  if (!raw) return [];
  try {
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed)
      ? parsed.filter(function (e) { return e && typeof e.id === 'string' && typeof e.at === 'number'; })
      : [];
  } catch (err) {
    return [];
  }
}

/** 동시에 도는 제출 트리거들이 서로의 변경을 덮어쓰지 않게 스크립트 잠금 안에서 고친다. */
function updateQueue_(mutate) {
  const lock = LockService.getScriptLock();
  lock.waitLock(30 * 1000);
  try {
    const next = mutate(readQueue_());
    const props = PropertiesService.getScriptProperties();
    if (next.length) {
      props.setProperty(BOOTH_QUEUE_PROPERTY, JSON.stringify(next));
    } else {
      props.deleteProperty(BOOTH_QUEUE_PROPERTY);
    }
    return next;
  } finally {
    lock.releaseLock();
  }
}

function enqueue_(responseId, submittedAt) {
  updateQueue_(function (queue) {
    const next = queue.filter(function (e) { return e.id !== responseId; });
    next.push({ id: responseId, at: submittedAt });
    next.sort(function (a, b) { return a.at - b.at; });
    if (next.length > BOOTH_QUEUE_MAX_ITEMS) {
      console.warn('[booth] 재전송 대기열이 가득 차 가장 오래된 '
        + (next.length - BOOTH_QUEUE_MAX_ITEMS) + '건을 버립니다.');
      return next.slice(next.length - BOOTH_QUEUE_MAX_ITEMS);
    }
    return next;
  });
}

function removeFromQueue_(responseId) {
  updateQueue_(function (queue) {
    return queue.filter(function (e) { return e.id !== responseId; });
  });
}

function findResponse_(form, responseId) {
  try {
    return form.getResponse(responseId);
  } catch (err) {
    return null;
  }
}

function hasNewerResponseForNumber_(form, response, number, config) {
  const key = numberKey_(number);
  if (!key) return false;
  const since = new Date(response.getTimestamp().getTime() + 1);
  return form.getResponses(since).some(function (other) {
    return other.getId() !== response.getId()
      && other.getTimestamp().getTime() > response.getTimestamp().getTime()
      && numberKey_(buildPayload_(other, config).number) === key;
  });
}


// ---------------------------------------------------------------------------
// 폼 응답 자동 삭제 (응답 내용은 읽지 않고 ID 와 제출 시각만 쓴다)
// ---------------------------------------------------------------------------

/**
 * 삭제 대상: 제출 시각 < now - 보유기간 (정확히 보유기간만큼 지난 응답은 아직 대상이 아니다).
 * 제출 시각이나 ID 를 읽을 수 없는 응답은 지우지 않고 invalid 로 센다. 대상은 오래된 순서.
 * (응답 수정 허용은 끈다(README 6). 수정된 응답은 제출 시각이 바뀔 수 있다.)
 */
function planExpiredResponses_(form, now) {
  const retentionDays = retentionDays_();
  const cutoff = now - retentionDays * BOOTH_DAY_MS;
  if (!Number.isFinite(now) || !Number.isFinite(cutoff)) {
    throw new Error('[booth] 폼 응답 삭제 기준 시각을 계산하지 못했습니다.');
  }
  const responses = form.getResponses();
  const expired = [];
  let invalid = 0;
  let oldestTime = null;
  for (let i = 0; i < responses.length; i++) {
    const timestamp = responses[i].getTimestamp();
    const time = timestamp && typeof timestamp.getTime === 'function' ? timestamp.getTime() : NaN;
    const id = responses[i].getId();
    if (!Number.isFinite(time) || typeof id !== 'string' || !id) {
      invalid += 1;
      continue;
    }
    if (oldestTime === null || time < oldestTime) {
      oldestTime = time;
    }
    if (time < cutoff) {
      expired.push({ id: id, time: time });
    }
  }
  expired.sort(function (a, b) { return a.time - b.time; });
  return {
    retentionDays: retentionDays, cutoff: cutoff, total: responses.length,
    expired: expired, invalid: invalid, oldestTime: oldestTime,
  };
}

/** 로그용 시각 (스크립트 시간대). */
function formatTime_(ms) {
  const date = new Date(ms);
  if (!Number.isFinite(date.getTime())) {
    return '(날짜 범위 밖)';
  }
  try {
    return Utilities.formatDate(date, Session.getScriptTimeZone(), 'yyyy-MM-dd HH:mm:ss z');
  } catch (err) {
    return date.toISOString();
  }
}

/** 오류 문구를 짧게. 응답 ID 처럼 긴 식별자는 가린다. */
function errorText_(err) {
  return String((err && err.message) || err).replace(/[A-Za-z0-9_-]{20,}/g, '(ID)').slice(0, 150);
}
