-- The baseline replaced public.user_profiles with public.profiles, but the
-- legacy auth.users trigger remained in production and made every new user
-- insert fail after attempting to write to the removed table.
drop trigger if exists create_default_user_profile_on_signup on auth.users;
drop function if exists public.create_default_user_profile();
