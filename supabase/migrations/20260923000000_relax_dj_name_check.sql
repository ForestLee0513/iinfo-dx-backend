-- dj_name 문자 제한 완화: 실제 IIDX DJ NAME에는 '!' 등 기호가 들어갈 수 있어
-- (예: AHMED!) 기존 정규식(^[A-Za-z0-9 .-]{1,6}$)이 프로필 동기화를 막았다.
-- 길이(1~6자)만 제한한다.
alter table iidx.profiles drop constraint if exists profiles_dj_name_check;
alter table iidx.profiles
  add constraint profiles_dj_name_check
  check (dj_name is null or char_length(dj_name) between 1 and 6);
