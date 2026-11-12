# Graph Report - Adherents  (2026-11-12)

## Corpus Check
- 272 files · ~194,510 words
- Verdict: corpus is large enough that graph structure adds value.
- Unclassified: 12 file(s) not represented in the graph (top: (none) 5, .css 4, .example 1)

## Summary
- 4691 nodes · 14203 edges · 163 communities (140 shown, 23 thin omitted)
- Extraction: 90% EXTRACTED · 10% INFERRED · 0% AMBIGUOUS · INFERRED: 1425 edges (avg confidence: 0.93)
- Token cost: 573,840 input · 0 output

## Community Hubs (Navigation)
- OIDC Consent Tests
- Vendored htmx
- Back-Channel Logout Tests
- Login & Member Tests
- Account Model
- Registration Dossiers
- Person & Bank Models
- Roles & Notifications
- Fees & Catalog Templates
- Login & OIDC Sessions
- Course Enrollments
- Blueprint Guard & CSP
- Fees Blueprint
- Directory Service
- TOTP Recovery Codes
- Pricing Tests
- Account 2FA Templates
- iCalendar Agenda Feed
- Email Link Tests
- Account Email & Password
- Household Fee Situation
- Seasons & E2E Tests
- Directory Blueprint
- Catalog Blueprint
- Attendance
- Enrollment Blueprint
- Lessons Blueprint
- Groups
- Retention & Guard
- Fee Items & Rules
- Agenda Tokens
- Lessons Service
- Directory Macros & Merge
- Fee Season Assessment
- CI Workflow
- Course Model
- Pricing Engine
- Room Calendar Jobs
- OIDC Keys & Logout Tokens
- Campaigns Blueprint
- Alembic Migrations
- CLI & Health Checks
- Course Waiting List
- Household & Campaign Models
- Notifications & Mailer
- Docker Compose
- Account Blueprint
- Course Detail Templates
- Lesson Templates
- Migration Tests
- Health & Permissions
- OIDC Blueprint
- Membership & Teachers
- Rooms & Workspace Sync
- Config & Startup Warnings
- Login Purge & Code Exchange
- Branding & Photos
- App Factory
- Assessments
- Catalog Service
- Journal & Anonymization
- Disciplines
- OIDC Authorization Codes
- Enrollment Step Templates
- Accounts Blueprint
- Planning Views
- Roles Admin
- Logout E2E Tests
- Campaign Templates
- Consent Revocation
- Catalog & List Filters
- Planning Week Grid
- Courses Service
- IBAN Formatting Tests
- Dashboard & Planning Templates
- Applications Blueprint
- Account Settings Templates
- Inventaire E2E
- Google Workspace Client
- Directory API Tests
- Settings Tests
- Teacher & Membership Forms
- Login Throttling
- Bank Data Docs
- CodeQL Workflow
- Two-Factor Login Flow
- Course Forms
- Accounts Admin Templates
- Discipline Forms
- Bank Key Rotation
- OIDC Client Model
- Test Fixtures
- IBAN Reveal
- Fee Errors & Season Copy
- Fees Docs
- Course Management Docs
- Error Pages
- User Guide & Plan
- Bank Account Forms
- Person Forms
- CSP Report Collection
- Lesson Model
- Fee Rule Editing
- OIDC Claims
- Developer Docs
- Changelog Features
- Error Tests
- OIDC Discovery & Export
- Catalogue (docs)
- References (catalog)
- Inscriptions (docs)
- Applications (blueprints)
- Pricing (services)
- Architecture (docs)
- Guard (services)
- Courses (services)
- Dependabot (.github)
- Env (migrations)
- Test Cli (tests)
- Directory (blueprints)
- Member (blueprints)
- Models (app)
- Bank (services)
- Pricing (services)
- Architecture (docs)
- Test Bank (tests)
- Oidc (blueprints)
- Lessons (services)
- Architecture (docs)
- Csp (blueprints)
- Readme (root)
- Requirements (root)
- .Pre-Commit-Config (root)
- Test Emails (tests)
- Test Logout (tests)
- Architecture (docs)
- Directory (services)
- Extensions (app)
- Index (applications)
- Changelog (root)
- Catalog (blueprints)
- Directory (blueprints)
- Icon-192 (icons)
- Roles (blueprints)
- Changelog (root)
- Changelog (root)
- Test Logout (tests)
- Directory (blueprints)
- Permissions (app)
- Notification (emails)
- Continue (oidc)
- Avatar (partials)
- Changelog (root)
- Changelog (root)
- Changelog (root)
- Entrypoint (root)
- Configuration (docs)
- Pyproject (root)

## God Nodes (most connected - your core abstractions)
1. `Role` - 243 edges
2. `log()` - 195 edges
3. `require()` - 177 edges
4. `make_account()` - 173 edges
5. `login()` - 173 edges
6. `Account` - 159 edges
7. `Activity` - 123 edges
8. `Flow` - 108 edges
9. `Course` - 101 edges
10. `Person` - 96 edges

## Surprising Connections (you probably didn't know these)
- `Back-Channel Logout (phase 4)` --semantically_similar_to--> `Deconnexion OIDC (sid, oidc_logout, review_access)`  [INFERRED] [semantically similar]
  CHANGELOG.md → docs/architecture.md
- `Serveur OIDC avec Authlib (phase 3)` --semantically_similar_to--> `Serveur OIDC (Authorization Code + PKCE S256)`  [INFERRED] [semantically similar]
  CHANGELOG.md → docs/architecture.md
- `test_single_head()` --uses--> `Config`  [INFERRED]
  tests/test_migrations.py → app/config.py
- `test_edit_refuses_a_taken_address()` --uses--> `Role`  [INFERRED]
  tests/test_accounts_admin.py → app/models.py
- `test_edit_warns_the_old_address()` --uses--> `Role`  [INFERRED]
  tests/test_accounts_admin.py → app/models.py

## Import Cycles
- None detected.

## Hyperedges (group relationships)
- **Gestion des cours en six etapes** — changelog_gestion_des_cours, changelog_cours_individuels, changelog_liste_d_attente, changelog_seances_et_calendrier, changelog_plannings, changelog_agenda_ics, changelog_agendas_salles_google_workspace, changelog_presences [EXTRACTED 1.00]
- **Protection des coordonnees bancaires** — docs_architecture_iban_chiffres, docs_architecture_mode_securise, docs_configuration_bank_data_keys, docs_architecture_double_authentification, docs_configuration_garde_fous [EXTRACTED 1.00]
- **Conventions pour ajouter un ecran** — docs_developpement_ajouter_un_ecran, docs_architecture_routes_valident_transaction, docs_architecture_can_require, docs_architecture_activity_log, docs_architecture_pas_de_js_en_ligne, docs_developpement_test_sweep_access [EXTRACTED 1.00]
- **Protection des IBAN** — docs_annuaire_bank_account, docs_annuaire_bank_data_keys, docs_annuaire_aes_gcm_encryption, docs_annuaire_iban_fingerprint, docs_annuaire_secure_mode, docs_annuaire_bank_rotate_key, docs_inscriptions_iban_dossier [EXTRACTED 1.00]
- **Parcours d'inscription : campagne → dossier → validation → annuaire** — docs_inscriptions_campaign, docs_inscriptions_dossier, docs_inscriptions_validation_dossier, docs_annuaire_membership, docs_cours_enrollment, docs_inscriptions_consent, docs_inscriptions_report_logiciel_de_gestion [EXTRACTED 1.00]
- **Vie des séances : génération, modifications, plannings, agendas, présences** — docs_cours_lesson, docs_cours_generation_seances, docs_cours_seances_modifiees_par_professeur, docs_cours_remplacant, docs_cours_plannings, docs_cours_agenda_ics, docs_cours_agendas_salles_google_workspace, docs_cours_attendance [EXTRACTED 1.00]
- **Two-factor authentication flow** — app_templates_account_totp_setup, app_templates_account_recovery_codes, app_templates_account_totp_active, app_templates_auth_two_factor [INFERRED 0.85]
- **Registration campaign dossier lifecycle (submit, validate, report)** — app_templates_campaigns_detail, app_templates_campaigns_dossiers, app_templates_campaigns_dossier, app_templates_campaigns_report [INFERRED 0.85]
- **OIDC client administration** — app_templates_applications_index, app_templates_applications_detail, app_templates_applications_form, app_templates_account_index_authorized_applications_list [INFERRED 0.85]
- **Household bank data under secure mode** — app_templates_directory_household, app_templates_directory_elevate, app_templates_directory_bank_form, app_templates_directory_bank_reveal, endpoint_directory_reveal_iban [INFERRED 0.85]
- **Course waiting list management** — app_templates_catalog_course_waiting_list, endpoint_catalog_add_to_waitlist, endpoint_catalog_move_waitlist, endpoint_catalog_enroll_from_waitlist, endpoint_catalog_remove_from_waitlist [EXTRACTED 1.00]
- **Household duplicate merge flow** — app_templates_directory_household, app_templates_directory_household_merge, app_templates_directory__merge_table, endpoint_directory_merge_household [EXTRACTED 1.00]
- **Enrollment dossier step pages** — app_templates_enrollment_dossier, app_templates_enrollment_household, app_templates_enrollment_student, app_templates_enrollment_consents, app_templates_enrollment_bank, app_templates_enrollment__step [EXTRACTED 1.00]
- **Person record management (view, edit, membership, merge)** — app_templates_directory_person, app_templates_directory_person_form, app_templates_directory_membership_form, app_templates_directory_person_merge [EXTRACTED 1.00]
- **Season management** — app_templates_directory_seasons, app_templates_directory_season_form, concept_current_season [INFERRED 0.95]
- **Flux de tarification : éléments, règles, réglages, cotisation** — app_templates_fees_index, app_templates_fees_rules, app_templates_fees_settings, app_templates_fees_household, app_templates_fees_simulator [INFERRED 0.85]
- **Pages à onglets des tarifs (fees/_nav tabs/head)** — app_templates_fees_assessments, app_templates_fees_courses, app_templates_fees_index, app_templates_fees_rules, app_templates_fees_settings, app_templates_fees_simulator [EXTRACTED 1.00]
- **Gestion des séances (macros when/state/cancel_form)** — app_templates_lessons__macros, app_templates_lessons_course, app_templates_lessons_mine, app_templates_lessons_edit, app_templates_lessons_calendar [INFERRED 0.85]
- **Settings & administration pages** — app_templates_settings_index, app_templates_settings_health, endpoint_health_page, endpoint_settings_index, app_templates_settings_index_branding_form [EXTRACTED 1.00]
- **OIDC identity provider UI flow** — app_templates_oidc_consent, app_templates_oidc_continue, app_templates_oidc_error, app_templates_oidc_logout [INFERRED 0.85]
- **Teacher management pages** — app_templates_teachers_index, app_templates_teachers_detail, app_templates_teachers_form, endpoint_teachers_create, endpoint_teachers_detail [EXTRACTED 1.00]

## Communities (163 total, 23 thin omitted)

### Community 0 - "OIDC Consent Tests"
Cohesion: 0.05
Nodes (91): OidcConsent, test_revoking_consent_notifies_the_app(), client_app(), code_of(), consent_page(), error_of(), Flow, follow() (+83 more)

### Community 1 - "Vendored htmx"
Cohesion: 0.08
Nodes (101): A(), ae(), an(), at(), B(), be(), bn(), bt() (+93 more)

### Community 2 - "Back-Channel Logout Tests"
Cohesion: 0.05
Nodes (80): OidcLogout, deliver_pending(), admin_http(), app_a(), app_b(), app_sessions(), browser(), connect() (+72 more)

### Community 3 - "Login & Member Tests"
Cohesion: 0.03
Nodes (62): login(), test_close_session_of_another_account_via_wrong_url(), account(), actions(), change(), fail(), test_account_without_password_cannot_log_in(), test_admin_role_constant_matches_builtin_list() (+54 more)

### Community 4 - "Account Model"
Cohesion: 0.04
Nodes (56): Account, _pending_invitation(), status_condition(), device_label(), test_another_device_is_closed_by_its_public_sid(), test_automatic_theme_follows_the_device(), test_close_another_device(), test_current_session_cannot_be_closed_by_this_route() (+48 more)

### Community 5 - "Registration Dossiers"
Cohesion: 0.06
Nodes (60): new_family(), send(), Consent, Dossier, access_url(), _apply(), blank_data(), changes() (+52 more)

### Community 6 - "Person & Bank Models"
Cohesion: 0.05
Nodes (55): BankAccount, ExternalRef, Person, Phone, move(), set_iban(), add_member(), household_merge_problems() (+47 more)

### Community 7 - "Roles & Notifications"
Cohesion: 0.06
Nodes (64): Role, admins(), notify(), enroll_totp(), make_account(), manager(), target(), test_even_a_role_with_every_right_cannot_touch_an_admin() (+56 more)

### Community 8 - "Fees & Catalog Templates"
Cohesion: 0.05
Nodes (74): catalog/_season_picker.html, Copy season catalog page (catalog/copy.html), Season copy preview plan, Workspace rooms sync page (catalog/workspace.html), Google Workspace room sync plan (new, linkable, unlinked, renamed, gone), Directory macros (directory/_macros.html), csrf() macro, phones() macro (+66 more)

### Community 9 - "Login & OIDC Sessions"
Cohesion: 0.05
Nodes (27): login(), LoginForm, LoginSession, OidcSession, _NoRedirect, _end(), end_client_sessions(), _NoRedirect (+19 more)

### Community 10 - "Course Enrollments"
Cohesion: 0.06
Nodes (49): Enrollment, enroll(), attending_enrollments(), actions(), adherent(), assessed(), course(), full() (+41 more)

### Community 11 - "Blueprint Guard & CSP"
Cohesion: 0.09
Nodes (7): no_store(), no_store(), no_store(), elevate(), elevated(), remaining(), _sid()

### Community 12 - "Fees Blueprint"
Cohesion: 0.08
Nodes (55): acknowledge_review(), add_pick(), assess_all(), assess_household(), _assessment(), assessments(), _back(), copy() (+47 more)

### Community 13 - "Directory Service"
Cohesion: 0.05
Nodes (40): fold_contains(), HouseholdMember, account_problems(), active_in(), add_phone(), change_role(), _comparable(), create_account() (+32 more)

### Community 14 - "TOTP Recovery Codes"
Cohesion: 0.08
Nodes (55): RecoveryCode, _digest(), new_recovery_codes(), use_recovery_code(), totp_code(), code_at(), count_sessions(), logged_in() (+47 more)

### Community 15 - "Pricing Tests"
Cohesion: 0.10
Nodes (60): Student, amounts(), cond(), famille(), rule(), run(), test_age_entre(), test_age_inconnu_classe_en_dernier() (+52 more)

### Community 16 - "Account 2FA Templates"
Cohesion: 0.04
Nodes (44): One-time recovery codes display, TOTP regenerate codes / disable form, TOTP QR code enrollment, Account create/edit form, Forgot password form, Login form, Two-factor verification form, Flash messages (+36 more)

### Community 17 - "iCalendar Agenda Feed"
Cohesion: 0.06
Nodes (48): _escape(), feed(), _fold(), _utc(), test_appel_refuse_a_une_famille(), actions(), at(), events_of() (+40 more)

### Community 18 - "Email Link Tests"
Cohesion: 0.07
Nodes (50): change(), forgot(), link_in(), member_with_smtp(), new_password(), smtp(), smtp_down(), test_address_taken_between_request_and_click() (+42 more)

### Community 19 - "Account Email & Password"
Cohesion: 0.07
Nodes (37): verify_email(), _grant_access(), _confirm_email(), forgot(), ForgotForm, logout(), NewPasswordForm, password() (+29 more)

### Community 20 - "Household Fee Situation"
Cohesion: 0.07
Nodes (46): FeePick, HouseholdSeason, add_pick(), band_of(), compute(), prepare(), resident_of(), save_settings() (+38 more)

### Community 21 - "Seasons & E2E Tests"
Cohesion: 0.07
Nodes (29): Season, shot(), test_a_household_from_start_to_iban(), test_directory_pages_have_no_violation(), test_one_membership_per_season_and_valid_dates(), test_season_with_memberships_cannot_be_deleted_in_the_database(), _seasons_and_teacher(), test_a_catalogue_from_start_to_the_next_season() (+21 more)

### Community 22 - "Directory Blueprint"
Cohesion: 0.10
Nodes (50): add_household_phone(), add_member(), add_person_phone(), _add_phone(), archive_household(), archive_person(), create_account(), create_household() (+42 more)

### Community 23 - "Catalog Blueprint"
Cohesion: 0.08
Nodes (42): add_to_waitlist(), archive_item(), _back_to(), copy(), course(), _course(), _course_choices(), course_csv() (+34 more)

### Community 24 - "Attendance"
Cohesion: 0.06
Nodes (36): Attendance, AttendanceError, family_visible(), history(), may_record(), record(), repeated_absences(), sheet() (+28 more)

### Community 25 - "Enrollment Blueprint"
Cohesion: 0.08
Nodes (34): access(), _access(), add_student(), bank_details(), BankForm, consents(), _data(), dossier() (+26 more)

### Community 26 - "Lessons Blueprint"
Cohesion: 0.09
Nodes (40): absences(), add(), add_break(), agenda_page(), AttendanceSettingsForm, _back_to_course(), BreakForm, calendar() (+32 more)

### Community 27 - "Groups"
Cohesion: 0.06
Nodes (34): index(), _apply(), create(), delete(), edit(), GroupForm, index(), AccessGroup (+26 more)

### Community 28 - "Retention & Guard"
Cohesion: 0.07
Nodes (31): index(), DurationForm, index(), Activity, AppSetting, forget(), set_current_season(), anonymize() (+23 more)

### Community 29 - "Fee Items & Rules"
Cohesion: 0.07
Nodes (35): FeeItem, FeeRule, Regie, save_item(), test_modification_journalisee(), admin_client(), flashes(), manager() (+27 more)

### Community 30 - "Agenda Tokens"
Cohesion: 0.06
Nodes (20): account_for(), _digest(), issue_token(), revoke_token(), InvalidIban, new_rum(), normalize_bic(), fernet() (+12 more)

### Community 31 - "Lessons Service"
Cohesion: 0.08
Nodes (31): add_extra(), _after_change(), _announce(), _before_delete(), cancel(), _check_teacher_may_change(), conflicts(), day_label() (+23 more)

### Community 32 - "Directory Macros & Merge"
Cohesion: 0.06
Nodes (47): address() macro, history() macro, pager() macro, Merge comparison table partial (directory/_merge_table.html), Merge field conflict choice, Bank details form (directory/bank_form.html), Encrypted IBAN with masked display, IBAN reveal page (directory/bank_reveal.html) (+39 more)

### Community 33 - "Fee Season Assessment"
Cohesion: 0.06
Nodes (33): delete_item(), FeeSeason, assess_season(), _clean_tags(), delete_item(), delete_rule(), describe_condition(), describe_conditions() (+25 more)

### Community 34 - "CI Workflow"
Cohesion: 0.05
Nodes (48): name, runs-on, steps, concurrency, cancel-in-progress, group, name, runs-on (+40 more)

### Community 35 - "Course Model"
Cohesion: 0.09
Nodes (31): Course, archive(), disciplines_of(), set_disciplines(), count(), course(), course_data(), test_a_course_adds_its_discipline_to_the_teacher() (+23 more)

### Community 36 - "Pricing Engine"
Cohesion: 0.08
Nodes (32): _apply_add(), _apply_line(), _cents(), _check(), compute(), _decimal(), _fact(), _int() (+24 more)

### Community 37 - "Room Calendar Jobs"
Cohesion: 0.07
Nodes (35): RoomCalendarJob, configured(), busy(), deliver_pending(), enabled(), _enqueue(), event_id(), lesson_changed() (+27 more)

### Community 38 - "OIDC Keys & Logout Tokens"
Cohesion: 0.07
Nodes (36): run_tasks(), OidcKey, _claim(), _deliver(), logout_token(), _next_delay(), post(), active_key() (+28 more)

### Community 39 - "Campaigns Blueprint"
Cohesion: 0.10
Nodes (32): add_consent(), _campaign(), CampaignForm, ConsentForm, _counts(), create(), _csv(), delete() (+24 more)

### Community 40 - "Alembic Migrations"
Cohesion: 0.08
Nodes (16): upgrade(), upgrade(), upgrade(), upgrade(), upgrade(), downgrade(), upgrade(), upgrade() (+8 more)

### Community 41 - "CLI & Health Checks"
Cohesion: 0.09
Nodes (30): register_cli(), bank_generate_key(), oidc_rotate_key(), run_tasks(), _check(), check_database(), check_email(), check_migrations() (+22 more)

### Community 42 - "Course Waiting List"
Cohesion: 0.08
Nodes (29): WaitlistEntry, add_to_waitlist(), announce_free_place(), _check_date(), CourseError, enroll_from_waitlist(), move_in_waitlist(), open_courses() (+21 more)

### Community 43 - "Household & Campaign Models"
Cohesion: 0.07
Nodes (21): AddressMixin, Campaign, Household, TimestampMixin, fill_and_send(), start(), test_a_new_family_receives_a_link(), test_a_parent_fills_and_sends_the_dossier() (+13 more)

### Community 44 - "Notifications & Mailer"
Cohesion: 0.09
Nodes (24): index(), mark_all_read(), test_email(), Notification, compose(), compose_test(), deliver_pending(), deliver_quietly() (+16 more)

### Community 45 - "Docker Compose"
Cohesion: 0.06
Nodes (39): build, condition, depends_on, env_file, environment, healthcheck, image, ports (+31 more)

### Community 46 - "Account Blueprint"
Cohesion: 0.10
Nodes (23): change_email(), _code_ok(), display(), _display_form(), DisplayForm, EmailForm, index(), logged_in() (+15 more)

### Community 47 - "Course Detail Templates"
Cohesion: 0.06
Nodes (34): Course detail page (catalog/course.html), Individual lesson slot form, Course waiting list, Withdrawal (abandon) form, Enrollment / course transfer form (catalog/enroll_form.html), Enroll student / change course form, Teacher contract (statut, arrivée, départ), Minor account creation with guardian consent (+26 more)

### Community 48 - "Lesson Templates"
Cohesion: 0.09
Nodes (39): Groupes (groups/index.html), Macros des séances (lessons/_macros.html), cancel_form(lesson, back) macro, state(lesson) macro, when(lesson) macro, Absences répétées (lessons/absences.html), Calendrier de saison (lessons/calendar.html), Séances d'un cours (lessons/course.html) (+31 more)

### Community 49 - "Migration Tests"
Cohesion: 0.10
Nodes (24): assert_fails(), run_sql(), test_attendance_on_the_real_schema(), test_catalog_on_the_real_schema(), test_constraints_of_the_real_schema(), test_deleting_an_account_keeps_the_journal_and_removes_what_belongs_to_it(), test_directory_on_the_real_schema(), test_enrollment_dates_and_review_on_the_real_schema() (+16 more)

### Community 50 - "Health & Permissions"
Cohesion: 0.07
Nodes (25): index(), _admin_only(), wrapped(), as_json(), _code(), page(), inject_globals(), load_user() (+17 more)

### Community 51 - "OIDC Blueprint"
Cohesion: 0.11
Nodes (24): authorize(), tell_client(), consent(), end_session(), end_session_confirm(), _error_uri(), _insecure_page(), jwks() (+16 more)

### Community 52 - "Membership & Teachers"
Cohesion: 0.08
Nodes (22): index(), Membership, Teacher, season(), test_seasons(), test_status_filters(), test_eleve_hors_de_la_feuille_refuse(), test_a_teacher_sees_his_students_and_their_guardians() (+14 more)

### Community 53 - "Rooms & Workspace Sync"
Cohesion: 0.09
Nodes (22): workspace_rooms(), fold(), Room, Site, rooms(), sorted_courses(), key(), students_of() (+14 more)

### Community 54 - "Config & Startup Warnings"
Cohesion: 0.08
Nodes (21): logo_file(), Config, startup_warnings(), TestConfig, test_issuer_https_without_proxy_warns(), test_account_pages_are_never_cached(), test_csrf_token_does_not_expire_during_a_long_form(), test_dark_theme_is_written_only_once() (+13 more)

### Community 55 - "Login Purge & Code Exchange"
Cohesion: 0.11
Nodes (32): purge_expired_logins(), test_code_is_refused_once_its_session_is_closed(), test_code_issued_before_the_upgrade_still_works(), test_failed_exchange_records_nothing(), base64_header(), decode(), exchange(), signed_in_code() (+24 more)

### Community 56 - "Branding & Photos"
Cohesion: 0.08
Nodes (15): _branding_form(), BrandingForm, _uuid(), Branding, current(), _delete_logo_file(), remove_logo(), save() (+7 more)

### Community 57 - "App Factory"
Cohesion: 0.13
Nodes (23): create_app(), app(), config_for(), _empty_tables(), release(), reset_database(), stamp_head(), _app_on() (+15 more)

### Community 58 - "Assessments"
Cohesion: 0.10
Nodes (23): Assessment, AssessmentLine, assess(), correct_line(), delete_assessment(), drift(), get_assessment(), _lines() (+15 more)

### Community 59 - "Catalog Service"
Cohesion: 0.10
Nodes (19): TeacherDiscipline, CatalogError, conflicts(), copy_plan(), copy_season(), CopyLine, CopyPlan, _count() (+11 more)

### Community 60 - "Journal & Anonymization"
Cohesion: 0.09
Nodes (23): anonymize(), purge_dossiers(), index(), remove_logo(), _render_index(), revoke_sessions(), save_branding(), set_logo() (+15 more)

### Community 61 - "Disciplines"
Cohesion: 0.08
Nodes (14): ArchivableMixin, Discipline, DisciplineType, Level, manager(), school(), teacher(), test_no_discipline_type_exists_at_first() (+6 more)

### Community 62 - "OIDC Authorization Codes"
Cohesion: 0.10
Nodes (15): OidcCode, OidcToken, digest(), find_token(), CodeGrant, _on_replay(), _save_token(), utcnow() (+7 more)

### Community 63 - "Enrollment Step Templates"
Cohesion: 0.08
Nodes (23): back() macro, Roll call (appel) attendance form, Attendance summary (présences/absences), courses_of() macro, Inventory loans display, loaned() macro, Mon espace (household self-service view), Enrollment campaign (+15 more)

### Community 64 - "Accounts Blueprint"
Cohesion: 0.12
Nodes (18): AccountForm, create(), cut(), detail(), edit(), invite(), _managed(), reset_totp() (+10 more)

### Community 65 - "Planning Views"
Cohesion: 0.10
Nodes (19): planning_view(), _range(), _render_planning(), typical_week(), week_lessons(), week_of(), _assign_lanes(), Event (+11 more)

### Community 66 - "Roles Admin"
Cohesion: 0.10
Nodes (19): _apply(), create(), delete(), edit(), _form_groups(), index(), _render(), RoleForm (+11 more)

### Community 67 - "Logout E2E Tests"
Cohesion: 0.12
Nodes (16): browser(), connected(), enter(), free_port(), json_of(), log_out_on_the_server(), page(), RelyingParty (+8 more)

### Community 68 - "Campaign Templates"
Cohesion: 0.08
Nodes (24): Campaign consents (copied text as proof), Campaign dossier status stats, Dossier directory change preview (before/after), Dossier decision (validate / send back / mark transferred), Campaign form (season, dates, allow new families), Report export to management software (CSV, optional IBAN), app/templates/directory/_macros.html, campaigns.add_consent (+16 more)

### Community 69 - "Consent Revocation"
Cohesion: 0.10
Nodes (17): deliver_after_request(), run(), init_app(), revoke_consent(), browser(), client_application(), json_of(), page() (+9 more)

### Community 70 - "Catalog & List Filters"
Cohesion: 0.08
Nodes (25): Accounts filters (role, status, group) and pagination, Dossiers list with status filter, Course create/edit form (catalog/course_form.html), Course form (discipline, level, teacher, schedule, room, color), Catalog index page (catalog/index.html), Catalog filters (discipline, teacher, day, site), Course schedule overlap warnings, Catalog tabs macro (+17 more)

### Community 71 - "Planning Week Grid"
Cohesion: 0.13
Nodes (19): week_layout(), enroll(), test_read_only_catalogue(), event(), page(), test_aujourd_hui_et_trait_de_l_heure(), test_compte_sans_fiche(), test_couleur_au_formulaire_et_recopiee() (+11 more)

### Community 72 - "Courses Service"
Cohesion: 0.13
Nodes (18): _by_name(), _course_span(), guardians_of(), _hhmm(), _minutes(), planning(), recipients(), roster() (+10 more)

### Community 73 - "IBAN Formatting Tests"
Cohesion: 0.11
Nodes (16): group(), Client, database(), home(), open_secure_mode(), secure(), test_a_used_code_cannot_open_the_secure_mode_twice(), test_bic() (+8 more)

### Community 74 - "Dashboard & Planning Templates"
Cohesion: 0.09
Nodes (22): Dashboard page (dashboard/index.html), Dashboard session stats, event_body() macro, keep() URL macro, Planning filters (par salle / professeur / site), planning-grid.css stylesheet, Planning views (semaine / mois / semaine type), brand_mark() macro (+14 more)

### Community 75 - "Applications Blueprint"
Cohesion: 0.17
Nodes (18): _apply(), _checked_groups(), create(), delete(), detail(), _discovery(), edit(), export_accounts() (+10 more)

### Community 76 - "Account Settings Templates"
Cohesion: 0.08
Nodes (22): Authorized applications (OIDC consents), Display preferences (theme, text size), Email change form, Profile form (first/last name), Back-channel logout notices, OIDC client configuration (redirect URIs, back-channel logout), OIDC application form (allowed/shared groups), Per-user theme / dark mode and text size (+14 more)

### Community 77 - "Inventaire E2E"
Cohesion: 0.17
Nodes (17): browser(), enter_inventaire(), free_port(), inventaire_logged_in(), inventaire_rows(), page(), sign_in_at_adherents(), test_1_fetch_then_link_by_an_admin() (+9 more)

### Community 78 - "Google Workspace Client"
Cohesion: 0.17
Nodes (13): access_token(), assertion(), buildings(), call(), _credentials(), delete_event(), events(), GoogleError (+5 more)

### Community 79 - "Directory API Tests"
Cohesion: 0.14
Nodes (16): admin_http(), basic(), fetch(), setup(), test_closed_app_cannot_read_it(), test_closed_by_default(), test_each_read_is_journaled_and_purged_like_logins(), test_export_button_downloads_the_same_list() (+8 more)

### Community 80 - "Settings Tests"
Cohesion: 0.13
Nodes (14): logo_setting(), png(), test_disconnect_everyone_but_me(), test_empty_values_go_back_to_defaults(), test_invalid_branding_is_refused(), test_logo_is_resized_and_served_publicly(), test_logo_metadata_is_dropped(), test_logo_route_serves_nothing_else() (+6 more)

### Community 81 - "Teacher & Membership Forms"
Cohesion: 0.12
Nodes (11): _blank(), edit_membership(), _strip(), _adult_born_before(), ContractForm, create(), detail(), edit() (+3 more)

### Community 82 - "Login Throttling"
Cohesion: 0.21
Nodes (13): ThrottleEvent, Limiter, purge(), count(), test_blocked_if_any_key_is(), test_blocks_at_the_limit(), test_clear_only_removes_those_keys(), test_digest_depends_on_secret_key() (+5 more)

### Community 83 - "Bank Data Docs"
Cohesion: 0.17
Nodes (22): AddressMixin, Chiffrement AES-GCM avec foyer en données associées, Table bank_account (IBAN chiffré), BANK_DATA_KEYS (clé dédiée), flask bank-rotate-key, Compte dès 16 ans avec accord du responsable, Annuaire : modèle de données, Table external_ref (+14 more)

### Community 84 - "CodeQL Workflow"
Cohesion: 0.10
Nodes (21): name, permissions, runs-on, steps, jobs, analyse, name, on (+13 more)

### Community 85 - "Two-Factor Login Flow"
Cohesion: 0.15
Nodes (12): CodeForm, _pending_account(), safe_next(), two_factor(), date_fr(), register_filters(), test_date_fr_always_shows_day_and_year(), test_date_fr_empty_values() (+4 more)

### Community 86 - "Course Forms"
Cohesion: 0.12
Nodes (9): CourseForm, create_course(), DisciplineTypeForm, edit_course(), LevelForm, RoomForm, _save_course(), SiteForm (+1 more)

### Community 87 - "Accounts Admin Templates"
Cohesion: 0.11
Nodes (17): Connected devices / sessions list, Account access actions (invite, cut, restore, reset 2FA), Account activity journal timeline, Account groups assignment form, Invitation / reset link display, accounts.create, accounts.cut, accounts.detail (+9 more)

### Community 88 - "Discipline Forms"
Cohesion: 0.14
Nodes (12): DisciplineForm, index(), references(), _with(), discipline_types(), disciplines(), levels(), room_open() (+4 more)

### Community 89 - "Bank Key Rotation"
Cohesion: 0.19
Nodes (13): bank_rotate_key(), BankKeyError, current_version(), _derive(), _dossier_aad(), fingerprint(), fingerprints(), keys() (+5 more)

### Community 90 - "OIDC Client Model"
Cohesion: 0.12
Nodes (7): OidcClient, create_form(), test_bad_logout_addresses_are_refused(), test_logout_addresses_are_optional(), test_logout_addresses_are_saved(), test_edit_form_shows_one_address_per_line_and_saves_unchanged(), test_toggle_and_delete()

### Community 91 - "Test Fixtures"
Cohesion: 0.14
Nodes (8): accounts(), admin(), blank_test_database(), fast_hashing(), fresh_database(), member(), module_database(), _worker_database()

### Community 92 - "IBAN Reveal"
Cohesion: 0.19
Nodes (15): _aad(), reveal(), same_iban(), bank_account(), household(), test_ciphertext_copied_to_another_household_cannot_be_read(), test_iban_never_appears_in_clear_anywhere_in_the_database(), test_manager_without_bank_right_sees_no_bank_section() (+7 more)

### Community 93 - "Fee Errors & Season Copy"
Cohesion: 0.15
Nodes (13): acknowledge_review(), copy_season(), create_regie(), FeeError, set_course_item(), _validate_bands(), item(), test_copie_refusee() (+5 more)

### Community 94 - "Fees Docs"
Cohesion: 0.19
Nodes (18): Table season / saison en cours, Arrondi, assessment / assessment_line (cotisation), Tarifs : éléments, règles et calcul, Droits fee.view / fee.edit / fee.assess / fee.situation, Éléments (fee_item), fee_pick, Règles (fee_rule : conditions, cible, effet) (+10 more)

### Community 95 - "Course Management Docs"
Cohesion: 0.19
Nodes (18): Absences répétées (seuil sans défaut), Agendas des salles (Google Workspace), Table attendance (présences), Couleur d'un cours (course.color), Gestion des cours, Droits enrollment.edit / lesson.manage / fee.assess, Génération des séances, app/services/google_workspace.py (+10 more)

### Community 96 - "Error Pages"
Cohesion: 0.20
Nodes (9): _back_url(), _is_api(), _json(), _page(), register_error_handlers(), http_error(), out_of_range(), overflow() (+1 more)

### Community 97 - "User Guide & Plan"
Cohesion: 0.12
Nodes (17): Calendrier d'une saison, Guide d'utilisation, Faire l'appel, Mes séances (professeurs), Qui peut faire quoi, Relier l'Inventaire à l'espace adhérent, Rôle Administrateur, Rôle Gestionnaire (secrétariat) (+9 more)

### Community 98 - "Bank Account Forms"
Cohesion: 0.15
Nodes (10): _bank_account(), BankAccountForm, create_bank_account(), create_mandate(), delete_bank_account(), edit_mandate(), MandateForm, reveal_iban() (+2 more)

### Community 99 - "Person Forms"
Cohesion: 0.15
Nodes (7): AddressForm, create_person(), edit_person(), edit_season(), MembershipForm, PersonForm, SeasonForm

### Community 100 - "CSP Report Collection"
Cohesion: 0.25
Nodes (10): logs(), post(), test_batch_size_is_bounded(), test_body_too_large(), test_log_injection_is_neutralised(), test_nothing_is_kept_in_the_journal(), test_odd_bodies(), test_rate_limit_is_per_address_and_committed() (+2 more)

### Community 101 - "Lesson Model"
Cohesion: 0.16
Nodes (5): Lesson, flag(), get(), positive_int(), test_ecran_du_calendrier()

### Community 102 - "Fee Rule Editing"
Cohesion: 0.18
Nodes (12): save_rule(), set_rule_active(), rule(), test_nouvelle_regle_en_brouillon_et_en_dernier(), test_regle_d_une_autre_saison(), test_regle_modifiee(), test_regle_qui_cite_un_element_d_une_autre_saison(), test_regle_refusee_par_le_moteur() (+4 more)

### Community 103 - "OIDC Claims"
Cohesion: 0.14
Nodes (4): claims_for(), init_app(), OpenID, StrictPKCE

### Community 104 - "Developer Docs"
Cohesion: 0.24
Nodes (10): CHANGELOG.md (Journal des versions), Modifier le modele de donnees (migrations, test_models_match_migrations), Acces de secours (flask create-admin), docs/README.md (index de la documentation), README.md (Adherents), Ce qui reste : import, reglements, bascule, hypothesis 6.170.14, playwright 1.64.1 (+2 more)

### Community 105 - "Changelog Features"
Cohesion: 0.16
Nodes (14): Agenda a s'abonner (.ics), Agendas des salles Google Workspace, Cotisation a revoir, Cours individuels (heure de passage), Gestion des cours (effectifs, attente, seances, plannings, presences), Liste d'attente d'un cours complet, Plannings (grille semaine, mois, couleur des cours), Presences (appel) (+6 more)

### Community 107 - "OIDC Discovery & Export"
Cohesion: 0.18
Nodes (8): discovery(), _endpoint(), oidc_export_accounts(), directory(), issuer(), may_access(), persons(), test_form_saves_and_journals_directory_access()

### Community 108 - "Catalogue (docs)"
Cohesion: 0.29
Nodes (13): Table teacher (profil professeur), Chevauchements de cours (avertissement), Copie d'une saison sur la suivante, Table course (cours d'une saison), Course.is_full(inscrits), Catalogue : modèle de données, Droits catalog.view / catalog.edit, Listes de référence (discipline_type, discipline, level, site, room) (+5 more)

### Community 109 - "References (catalog)"
Cohesion: 0.26
Nodes (12): Reference item form (catalog/item_form.html), Disciplines, levels and places page (catalog/references.html), Archive-instead-of-delete for used references, row_actions macro, catalog.archive_item, catalog.create_item, catalog.delete_item, catalog.edit_item (+4 more)

### Community 110 - "Inscriptions (docs)"
Cohesion: 0.29
Nodes (12): Table campaign (campagne), Table campaign_consent, Table consent, Conservation et anonymisation, Inscriptions : campagnes, dossiers, Table dossier (dossier du foyer), Formulaire public nouvelles familles (lien 30 jours), IBAN saisi par les familles (chiffré, lié au dossier) (+4 more)

### Community 111 - "Applications (blueprints)"
Cohesion: 0.22
Nodes (4): ApplicationForm, _check_uris(), all_clients(), redirect_uri_problem()

### Community 112 - "Pricing (services)"
Cohesion: 0.20
Nodes (9): Household, Item, Order, test_simulation_d_un_foyer_fictif(), test_conditions_et(), test_conditions_ou(), test_groupe_exclusif_la_premiere_l_emporte(), test_pourcentage_arrondi_au_centime_le_plus_proche() (+1 more)

### Community 113 - "Architecture (docs)"
Cohesion: 0.20
Nodes (8): Socle repris de l'Inventaire (phase 0), activity.log() (journal dans la meme transaction), can(user, action) et @require, Roles et droits (Administrateur, Gestionnaire, Adherent), Ajouter un ecran (procedure), Base de test PostgreSQL adherents_test (fresh_database, xdist), test_sweep.py ACCESS (toutes routes x tous roles), pytest-xdist 3.8.0

### Community 114 - "Guard (services)"
Cohesion: 0.20
Nodes (5): count_reads(), count_reads(), count_reads(), count_read(), _reads()

### Community 115 - "Courses (services)"
Cohesion: 0.22
Nodes (7): flag_review(), transfer(), test_autre_saison_non_marquee(), test_chaque_changement_marque_la_cotisation_de_chaque_foyer(), test_motifs_sans_doublon_et_limites(), test_transfert_vers_un_cours_quitte_reprend_l_inscription(), _transfer()

### Community 116 - "Dependabot (.github)"
Cohesion: 0.20
Nodes (9): mineures, update-types, interval, updates, directory, groups, package-ecosystem, schedule (+1 more)

### Community 117 - "Env (migrations)"
Cohesion: 0.29
Nodes (5): get_engine(), get_engine_url(), get_metadata(), run_migrations_offline(), run_migrations_online()

### Community 118 - "Test Cli (tests)"
Cohesion: 0.31
Nodes (7): create_admin(), make_app_with_members(), test_create_admin(), test_create_admin_is_the_rescue_access(), test_export_lists_only_the_accounts_that_may_enter(), test_export_never_contains_secrets(), test_export_to_a_file_keeps_accents()

### Community 119 - "Directory (blueprints)"
Cohesion: 0.22
Nodes (6): CodeForm, elevate(), leave_secure(), _safe_next(), wrapped(), drop_elevation()

### Community 120 - "Member (blueprints)"
Cohesion: 0.28
Nodes (5): index(), _seasons_of(), enabled(), loans(), test_inventaire_needs_https_and_a_token()

### Community 121 - "Models (app)"
Cohesion: 0.25
Nodes (6): SepaMandate, _months_ago(), purge_expired_mandates(), test_every_bank_operation_needs_the_secure_mode(), test_expired_mandates_are_purged_with_their_last_bank_account(), test_months_ago_handles_month_ends()

### Community 122 - "Bank (services)"
Cohesion: 0.28
Nodes (6): check_startup(), new_key(), parse_keys(), test_malformed_keys_are_refused(), test_several_keys_and_the_highest_encrypts(), test_non_utf8_basic_credentials_are_refused_cleanly()

### Community 124 - "Architecture (docs)"
Cohesion: 0.22
Nodes (7): CSP bloquante (default-src 'self'), Recherche sans accents : fold() / fold_contains() / unaccent, Limitation des tentatives (throttle_event), TRUST_PROXY / BIND_ADDR / CSP_REPORT_ONLY, Reverse proxy HTTPS (Caddy) vers 127.0.0.1:8001, Sauvegarde et restauration (sqlite3 .backup), psycopg[binary] 3.3.8

### Community 125 - "Test Bank (tests)"
Cohesion: 0.29
Nodes (4): normalize_iban(), test_foreign_iban_of_unknown_length_is_checked_by_its_key(), test_iban_is_normalized(), test_invalid_iban_is_refused()

### Community 126 - "Oidc (blueprints)"
Cohesion: 0.29
Nodes (4): directory(), _directory_refused(), persons(), find_client()

### Community 127 - "Lessons (services)"
Cohesion: 0.25
Nodes (4): events(), personal_lessons(), student_times(), upcoming_for()

### Community 128 - "Architecture (docs)"
Cohesion: 0.25
Nodes (8): IBAN chiffres AES-GCM (BANK_DATA_KEYS), Limite de lecture massive de l'annuaire, Mode securise (bank.view + TOTP recent), BANK_DATA_KEYS (rotation de cle), Garde-fous (ELEVATION_MINUTES, DIRECTORY_READ_LIMIT, IDLE_TIMEOUT_HOURS), OIDC_ISSUER, Mise en service Docker Compose (db, app, scheduler), cryptography 50.0.2

### Community 129 - "Csp (blueprints)"
Cohesion: 0.29
Nodes (3): _clean(), report(), _reports()

### Community 130 - "Readme (root)"
Cohesion: 0.29
Nodes (7): Mon espace (phase 7), INVENTAIRE_API_URL / INVENTAIRE_API_TOKEN, /oidc/personnes (materiel prete), Essayer avec l'Inventaire en local (ports 8001/8000), Adherents (appli d'association), Fournisseur d'identite OpenID Connect, Inventaire (appli cliente, materiel prete)

### Community 131 - "Requirements (root)"
Cohesion: 0.33
Nodes (5): Serveur OIDC avec Authlib (phase 3), Serveur OIDC (Authorization Code + PKCE S256), Authlib 1.9.5, Flask 3.1.3, SQLAlchemy 2.1.6 / Flask-SQLAlchemy / Flask-Migrate

### Community 132 - ".Pre-Commit-Config (root)"
Cohesion: 0.29
Nodes (6): args, id, repos, hooks, repo, rev

### Community 134 - "Test Logout (tests)"
Cohesion: 0.38
Nodes (3): receiver(), Receiver, test_redirect_is_not_followed()

### Community 135 - "Architecture (docs)"
Cohesion: 0.33
Nodes (6): Back-Channel Logout (phase 4), Deconnexion OIDC (sid, oidc_logout, review_access), Sessions revocables (LoginSession), Taches periodiques (flask run-tasks, scheduler), Donner un role dans une appli via groupes transmis, Page /sante (etat de l'installation)

### Community 136 - "Directory (services)"
Cohesion: 0.40
Nodes (3): phone_filter(), display_phone(), test_phone_display()

### Community 137 - "Extensions (app)"
Cohesion: 0.40
Nodes (3): _is_write_request(), _write_lock(), test_authorize_takes_the_write_lock_from_the_start()

### Community 138 - "Index (applications)"
Cohesion: 0.40
Nodes (4): Signing keys list and rotation, applications.create, applications.detail, applications.rotate_key

### Community 139 - "Changelog (root)"
Cohesion: 0.40
Nodes (5): Comptes : invitation, TOTP, groupes (phase 2), Double authentification TOTP, SECRET_KEY, Incident de securite, pyotp 2.9.0

### Community 142 - "Icon-192 (icons)"
Cohesion: 0.83
Nodes (4): App Icon 192px (icon-192.png), App Icon 512px (icon-512.png), PWA Icon Set (192/512), Visual Identity: White QR-Code Motif on Burgundy Square

### Community 145 - "Changelog (root)"
Cohesion: 0.67
Nodes (3): Catalogue (phase 8), Types de discipline composables, Modele catalogue (discipline, course, room...)

### Community 146 - "Changelog (root)"
Cohesion: 0.67
Nodes (3): Outillage : Ruff, CI GitHub Actions, CodeQL, pip-audit, Qualite du code (Ruff, pre-commit, pip-audit, CI, CodeQL), ruff 0.17.4 / pre-commit

## Ambiguous Edges - Review These
- `PostgreSQL remplace SQLite` → `Sauvegarde et restauration (sqlite3 .backup)`  [AMBIGUOUS]
  docs/installation.md · relation: conceptually_related_to

## Knowledge Gaps
- **253 isolated node(s):** `adherents`, `Liste d'attente d'un cours complet`, `Cours individuels (heure de passage)`, `Presences (appel)`, `Types de discipline composables` (+248 more)
  These have ≤1 connection - possible missing edges or undocumented components. (Counts symbols only; 1325 node(s) total have ≤1 connection when file, concept and rationale nodes are included.)
- **23 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `log()` connect `Journal & Anonymization` to `Registration Dossiers`, `Person & Bank Models`, `Roles & Notifications`, `Login & OIDC Sessions`, `Course Enrollments`, `Blueprint Guard & CSP`, `Fees Blueprint`, `Account Email & Password`, `Household Fee Situation`, `Directory Blueprint`, `Catalog Blueprint`, `Attendance`, `Enrollment Blueprint`, `Groups`, `Retention & Guard`, `Fee Items & Rules`, `Agenda Tokens`, `Lessons Service`, `Fee Season Assessment`, `Room Calendar Jobs`, `OIDC Keys & Logout Tokens`, `Campaigns Blueprint`, `CLI & Health Checks`, `Course Waiting List`, `Household & Campaign Models`, `Account Blueprint`, `Health & Permissions`, `OIDC Blueprint`, `Rooms & Workspace Sync`, `Assessments`, `OIDC Authorization Codes`, `Accounts Blueprint`, `Roles Admin`, `Courses Service`, `Applications Blueprint`, `Teacher & Membership Forms`, `Two-Factor Login Flow`, `Course Forms`, `Bank Key Rotation`, `Fee Errors & Season Copy`, `Bank Account Forms`, `Person Forms`, `Fee Rule Editing`, `OIDC Discovery & Export`, `Guard (services)`, `Courses (services)`, `Directory (blueprints)`, `Oidc (blueprints)`?**
  _High betweenness centrality (0.048) - this node is a cross-community bridge._
- **Are the 162 inferred relationships involving `Role` (e.g. with `AccountForm` and `_apply()`) actually correct?**
  _`Role` has 162 INFERRED edges - model-reasoned connections that need verification._
- **What connects `adherents`, `Liste d'attente d'un cours complet`, `Cours individuels (heure de passage)` to the rest of the system?**
  _253 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `OIDC Consent Tests` be split into smaller, more focused modules?**
  _Cohesion score 0.050353925353925355 - nodes in this community are weakly interconnected._
- **Why does `Account` connect `Account Model` to `Back-Channel Logout Tests`, `Login & Member Tests`, `Person & Bank Models`, `Roles & Notifications`, `Login & OIDC Sessions`, `Blueprint Guard & CSP`, `Directory Service`, `TOTP Recovery Codes`, `Email Link Tests`, `Account Email & Password`, `Test Logout (tests)`, `Seasons & E2E Tests`, `Directory Blueprint`, `Groups`, `Retention & Guard`, `Agenda Tokens`, `CLI & Health Checks`, `Household & Campaign Models`, `Notifications & Mailer`, `Account Blueprint`, `Health & Permissions`, `App Factory`, `Accounts Blueprint`, `Roles Admin`, `Logout E2E Tests`, `Consent Revocation`, `Inventaire E2E`, `Two-Factor Login Flow`, `Test Fixtures`, `OIDC Discovery & Export`, `Test Cli (tests)`?**
  _High betweenness centrality (0.044) - this node is a cross-community bridge._
- **Are the 2 inferred relationships involving `make_account()` (e.g. with `.set_password()` and `Role`) actually correct?**
  _`make_account()` has 2 INFERRED edges - model-reasoned connections that need verification._

### Low-confidence Hints
_AMBIGUOUS edges — the extractor was unsure. Verify before acting on these._

- **What is the exact relationship between `PostgreSQL remplace SQLite` and `Sauvegarde et restauration (sqlite3 .backup)`?**
  _Edge tagged AMBIGUOUS (relation: conceptually_related_to) - confidence is low._