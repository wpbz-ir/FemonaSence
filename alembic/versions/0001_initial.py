"""initial Cinema Vault domain schema

Revision ID: 0001_initial
Revises:
Create Date: 2026-09-24

This revision is self-contained and does not import current ORM metadata
for the upgrade/downgrade operations.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0001_initial"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

INITIAL_SQL = [
    r"""CREATE TABLE collections (
	name_fa VARCHAR(160) NOT NULL, 
	name_en VARCHAR(160), 
	slug VARCHAR(180) NOT NULL, 
	description TEXT, 
	poster_url TEXT, 
	parent_id UUID, 
	sort_order INTEGER NOT NULL, 
	active BOOLEAN NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_collections PRIMARY KEY (id), 
	CONSTRAINT uq_collections_slug UNIQUE (slug), 
	CONSTRAINT fk_collections_parent_id_collections FOREIGN KEY(parent_id) REFERENCES collections (id) ON DELETE SET NULL
)

""",
    r"""CREATE TABLE countries (
	name_fa VARCHAR(100) NOT NULL, 
	name_en VARCHAR(100) NOT NULL, 
	code VARCHAR(8) NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_countries PRIMARY KEY (id), 
	CONSTRAINT uq_countries_code UNIQUE (code)
)

""",
    r"""CREATE TABLE genres (
	name_fa VARCHAR(100) NOT NULL, 
	name_en VARCHAR(100) NOT NULL, 
	slug VARCHAR(120) NOT NULL, 
	active BOOLEAN NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_genres PRIMARY KEY (id), 
	CONSTRAINT uq_genres_slug UNIQUE (slug)
)

""",
    r"""CREATE TABLE membership_channels (
	telegram_chat_id BIGINT NOT NULL, 
	username VARCHAR(255), 
	title VARCHAR(255) NOT NULL, 
	invite_url TEXT, 
	required BOOLEAN NOT NULL, 
	active BOOLEAN NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_membership_channels PRIMARY KEY (id), 
	CONSTRAINT uq_membership_channels_chat UNIQUE (telegram_chat_id)
)

""",
    r"""CREATE TABLE notification_templates (
	code VARCHAR(64) NOT NULL, 
	title VARCHAR(255) NOT NULL, 
	body TEXT NOT NULL, 
	active BOOLEAN NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_notification_templates PRIMARY KEY (id), 
	CONSTRAINT uq_notification_templates_code UNIQUE (code)
)

""",
    r"""CREATE TABLE people (
	name_fa VARCHAR(160), 
	name_en VARCHAR(160) NOT NULL, 
	slug VARCHAR(180) NOT NULL, 
	biography TEXT, 
	profile_url TEXT, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_people PRIMARY KEY (id), 
	CONSTRAINT uq_people_slug UNIQUE (slug)
)

""",
    r"""CREATE TABLE permissions (
	code VARCHAR(100) NOT NULL, 
	description VARCHAR(255), 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_permissions PRIMARY KEY (id), 
	CONSTRAINT uq_permissions_code UNIQUE (code)
)

""",
    r"""CREATE TABLE plans (
	code VARCHAR(32) NOT NULL, 
	name_fa VARCHAR(120) NOT NULL, 
	name_en VARCHAR(120), 
	description TEXT, 
	price_irr NUMERIC(18, 2) NOT NULL, 
	duration_days INTEGER NOT NULL, 
	rank INTEGER NOT NULL, 
	active BOOLEAN NOT NULL, 
	sort_order INTEGER NOT NULL, 
	features JSONB NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_plans PRIMARY KEY (id), 
	CONSTRAINT uq_plans_code UNIQUE (code)
)

""",
    r"""CREATE TABLE roles (
	name VARCHAR(64) NOT NULL, 
	description VARCHAR(255), 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_roles PRIMARY KEY (id), 
	CONSTRAINT uq_roles_name UNIQUE (name)
)

""",
    r"""CREATE TABLE storage_providers (
	code VARCHAR(32) NOT NULL, 
	name VARCHAR(100) NOT NULL, 
	provider_type VARCHAR(32) NOT NULL, 
	active BOOLEAN NOT NULL, 
	config JSONB NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_storage_providers PRIMARY KEY (id), 
	CONSTRAINT uq_storage_providers_code UNIQUE (code)
)

""",
    r"""CREATE TABLE titles (
	kind VARCHAR(16) NOT NULL, 
	title_fa VARCHAR(255) NOT NULL, 
	title_en VARCHAR(255), 
	original_title VARCHAR(255), 
	slug VARCHAR(280) NOT NULL, 
	synopsis TEXT, 
	release_year INTEGER, 
	runtime_minutes INTEGER, 
	imdb_id VARCHAR(32), 
	imdb_rating FLOAT, 
	imdb_votes INTEGER, 
	user_rating FLOAT, 
	poster_url TEXT, 
	backdrop_url TEXT, 
	trailer_url TEXT, 
	status VARCHAR(32) NOT NULL, 
	published_at TIMESTAMP WITH TIME ZONE, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_titles PRIMARY KEY (id), 
	CONSTRAINT uq_titles_slug UNIQUE (slug), 
	CONSTRAINT uq_titles_imdb_id UNIQUE (imdb_id)
)

""",
    r"""CREATE TABLE users (
	telegram_user_id BIGINT NOT NULL, 
	username VARCHAR(64), 
	first_name VARCHAR(128), 
	last_name VARCHAR(128), 
	language_code VARCHAR(16), 
	status VARCHAR(32) NOT NULL, 
	last_seen_at TIMESTAMP WITH TIME ZONE, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_users PRIMARY KEY (id), 
	CONSTRAINT uq_users_telegram_user_id UNIQUE (telegram_user_id)
)

""",
    r"""CREATE TABLE audit_logs (
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	actor_user_id UUID, 
	action VARCHAR(100) NOT NULL, 
	entity_type VARCHAR(64) NOT NULL, 
	entity_id UUID, 
	old_value JSONB NOT NULL, 
	new_value JSONB NOT NULL, 
	ip_address VARCHAR(64), 
	id UUID NOT NULL, 
	CONSTRAINT pk_audit_logs PRIMARY KEY (id), 
	CONSTRAINT fk_audit_logs_actor_user_id_users FOREIGN KEY(actor_user_id) REFERENCES users (id) ON DELETE SET NULL
)

""",
    r"""CREATE TABLE favorites (
	user_id UUID NOT NULL, 
	title_id UUID NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_favorites PRIMARY KEY (id), 
	CONSTRAINT uq_favorites_user_title UNIQUE (user_id, title_id), 
	CONSTRAINT fk_favorites_user_id_users FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE, 
	CONSTRAINT fk_favorites_title_id_titles FOREIGN KEY(title_id) REFERENCES titles (id) ON DELETE CASCADE
)

""",
    r"""CREATE TABLE orders (
	order_number VARCHAR(40) NOT NULL, 
	user_id UUID NOT NULL, 
	plan_id UUID, 
	amount_irr NUMERIC(18, 2) NOT NULL, 
	currency VARCHAR(8) NOT NULL, 
	status VARCHAR(32) NOT NULL, 
	description TEXT, 
	metadata JSONB NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_orders PRIMARY KEY (id), 
	CONSTRAINT uq_orders_order_number UNIQUE (order_number), 
	CONSTRAINT fk_orders_user_id_users FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE RESTRICT, 
	CONSTRAINT fk_orders_plan_id_plans FOREIGN KEY(plan_id) REFERENCES plans (id) ON DELETE RESTRICT
)

""",
    r"""CREATE TABLE referral_codes (
	user_id UUID NOT NULL, 
	code VARCHAR(64) NOT NULL, 
	active BOOLEAN NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_referral_codes PRIMARY KEY (id), 
	CONSTRAINT uq_referral_codes_code UNIQUE (code), 
	CONSTRAINT uq_referral_codes_user_id UNIQUE (user_id), 
	CONSTRAINT fk_referral_codes_user_id_users FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE
)

""",
    r"""CREATE TABLE role_permissions (
	role_id UUID NOT NULL, 
	permission_id UUID NOT NULL, 
	CONSTRAINT pk_role_permissions PRIMARY KEY (role_id, permission_id), 
	CONSTRAINT fk_role_permissions_role_id_roles FOREIGN KEY(role_id) REFERENCES roles (id) ON DELETE CASCADE, 
	CONSTRAINT fk_role_permissions_permission_id_permissions FOREIGN KEY(permission_id) REFERENCES permissions (id) ON DELETE CASCADE
)

""",
    r"""CREATE TABLE series (
	title_id UUID NOT NULL, 
	total_seasons INTEGER NOT NULL, 
	ongoing BOOLEAN NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_series PRIMARY KEY (id), 
	CONSTRAINT uq_series_title_id UNIQUE (title_id), 
	CONSTRAINT fk_series_title_id_titles FOREIGN KEY(title_id) REFERENCES titles (id) ON DELETE CASCADE
)

""",
    r"""CREATE TABLE storage_files (
	provider_id UUID NOT NULL, 
	chat_id BIGINT, 
	message_id BIGINT, 
	file_id TEXT, 
	file_unique_key VARCHAR(255) NOT NULL, 
	filename TEXT, 
	mime_type VARCHAR(128), 
	size_bytes BIGINT, 
	checksum_sha256 VARCHAR(64), 
	metadata JSONB NOT NULL, 
	status VARCHAR(32) NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_storage_files PRIMARY KEY (id), 
	CONSTRAINT uq_storage_files_provider_unique UNIQUE (provider_id, file_unique_key), 
	CONSTRAINT fk_storage_files_provider_id_storage_providers FOREIGN KEY(provider_id) REFERENCES storage_providers (id) ON DELETE RESTRICT
)

""",
    r"""CREATE TABLE subscriptions (
	user_id UUID NOT NULL, 
	plan_id UUID NOT NULL, 
	starts_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	expires_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	status VARCHAR(32) NOT NULL, 
	auto_renew BOOLEAN NOT NULL, 
	cancelled_at TIMESTAMP WITH TIME ZONE, 
	metadata JSONB NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_subscriptions PRIMARY KEY (id), 
	CONSTRAINT fk_subscriptions_user_id_users FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE, 
	CONSTRAINT fk_subscriptions_plan_id_plans FOREIGN KEY(plan_id) REFERENCES plans (id) ON DELETE RESTRICT
)

""",
    r"""CREATE TABLE title_collections (
	title_id UUID NOT NULL, 
	collection_id UUID NOT NULL, 
	CONSTRAINT pk_title_collections PRIMARY KEY (title_id, collection_id), 
	CONSTRAINT fk_title_collections_title_id_titles FOREIGN KEY(title_id) REFERENCES titles (id) ON DELETE CASCADE, 
	CONSTRAINT fk_title_collections_collection_id_collections FOREIGN KEY(collection_id) REFERENCES collections (id) ON DELETE CASCADE
)

""",
    r"""CREATE TABLE title_countries (
	title_id UUID NOT NULL, 
	country_id UUID NOT NULL, 
	CONSTRAINT pk_title_countries PRIMARY KEY (title_id, country_id), 
	CONSTRAINT fk_title_countries_title_id_titles FOREIGN KEY(title_id) REFERENCES titles (id) ON DELETE CASCADE, 
	CONSTRAINT fk_title_countries_country_id_countries FOREIGN KEY(country_id) REFERENCES countries (id) ON DELETE CASCADE
)

""",
    r"""CREATE TABLE title_genres (
	title_id UUID NOT NULL, 
	genre_id UUID NOT NULL, 
	CONSTRAINT pk_title_genres PRIMARY KEY (title_id, genre_id), 
	CONSTRAINT fk_title_genres_title_id_titles FOREIGN KEY(title_id) REFERENCES titles (id) ON DELETE CASCADE, 
	CONSTRAINT fk_title_genres_genre_id_genres FOREIGN KEY(genre_id) REFERENCES genres (id) ON DELETE CASCADE
)

""",
    r"""CREATE TABLE title_people (
	title_id UUID NOT NULL, 
	person_id UUID NOT NULL, 
	role VARCHAR(32) NOT NULL, 
	CONSTRAINT pk_title_people PRIMARY KEY (title_id, person_id, role), 
	CONSTRAINT fk_title_people_title_id_titles FOREIGN KEY(title_id) REFERENCES titles (id) ON DELETE CASCADE, 
	CONSTRAINT fk_title_people_person_id_people FOREIGN KEY(person_id) REFERENCES people (id) ON DELETE CASCADE
)

""",
    r"""CREATE TABLE user_roles (
	user_id UUID NOT NULL, 
	role_id UUID NOT NULL, 
	CONSTRAINT pk_user_roles PRIMARY KEY (user_id, role_id), 
	CONSTRAINT fk_user_roles_user_id_users FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE, 
	CONSTRAINT fk_user_roles_role_id_roles FOREIGN KEY(role_id) REFERENCES roles (id) ON DELETE CASCADE
)

""",
    r"""CREATE TABLE wallets (
	user_id UUID NOT NULL, 
	balance_irr NUMERIC(18, 2) NOT NULL, 
	version INTEGER NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_wallets PRIMARY KEY (id), 
	CONSTRAINT uq_wallets_user_id UNIQUE (user_id), 
	CONSTRAINT fk_wallets_user_id_users FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE
)

""",
    r"""CREATE TABLE notification_jobs (
	user_id UUID NOT NULL, 
	template_id UUID, 
	subscription_id UUID, 
	notification_type VARCHAR(64) NOT NULL, 
	dedupe_key VARCHAR(180) NOT NULL, 
	run_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	status VARCHAR(32) NOT NULL, 
	attempts INTEGER NOT NULL, 
	last_error TEXT, 
	sent_at TIMESTAMP WITH TIME ZONE, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_notification_jobs PRIMARY KEY (id), 
	CONSTRAINT uq_notification_jobs_dedupe UNIQUE (dedupe_key), 
	CONSTRAINT fk_notification_jobs_user_id_users FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE, 
	CONSTRAINT fk_notification_jobs_template_id_notification_templates FOREIGN KEY(template_id) REFERENCES notification_templates (id) ON DELETE SET NULL, 
	CONSTRAINT fk_notification_jobs_subscription_id_subscriptions FOREIGN KEY(subscription_id) REFERENCES subscriptions (id) ON DELETE CASCADE
)

""",
    r"""CREATE TABLE payment_attempts (
	order_id UUID NOT NULL, 
	provider VARCHAR(32) NOT NULL, 
	authority VARCHAR(255), 
	payment_url TEXT, 
	provider_invoice_id VARCHAR(255), 
	status VARCHAR(32) NOT NULL, 
	requested_amount_irr NUMERIC(18, 2) NOT NULL, 
	raw_callback JSONB NOT NULL, 
	error_message TEXT, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_payment_attempts PRIMARY KEY (id), 
	CONSTRAINT uq_payment_attempt_provider_authority UNIQUE (provider, authority), 
	CONSTRAINT fk_payment_attempts_order_id_orders FOREIGN KEY(order_id) REFERENCES orders (id) ON DELETE CASCADE
)

""",
    r"""CREATE TABLE referrals (
	referrer_user_id UUID NOT NULL, 
	referred_user_id UUID NOT NULL, 
	referral_code_id UUID NOT NULL, 
	status VARCHAR(32) NOT NULL, 
	qualified_at TIMESTAMP WITH TIME ZONE, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_referrals PRIMARY KEY (id), 
	CONSTRAINT uq_referrals_referred_user UNIQUE (referred_user_id), 
	CONSTRAINT fk_referrals_referrer_user_id_users FOREIGN KEY(referrer_user_id) REFERENCES users (id) ON DELETE RESTRICT, 
	CONSTRAINT fk_referrals_referred_user_id_users FOREIGN KEY(referred_user_id) REFERENCES users (id) ON DELETE RESTRICT, 
	CONSTRAINT fk_referrals_referral_code_id_referral_codes FOREIGN KEY(referral_code_id) REFERENCES referral_codes (id) ON DELETE RESTRICT
)

""",
    r"""CREATE TABLE seasons (
	series_id UUID NOT NULL, 
	season_number INTEGER NOT NULL, 
	title VARCHAR(255), 
	synopsis TEXT, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_seasons PRIMARY KEY (id), 
	CONSTRAINT uq_seasons_series_number UNIQUE (series_id, season_number), 
	CONSTRAINT fk_seasons_series_id_series FOREIGN KEY(series_id) REFERENCES series (id) ON DELETE CASCADE
)

""",
    r"""CREATE TABLE wallet_ledger_entries (
	wallet_id UUID NOT NULL, 
	amount_irr NUMERIC(18, 2) NOT NULL, 
	balance_after_irr NUMERIC(18, 2) NOT NULL, 
	entry_type VARCHAR(32) NOT NULL, 
	external_reference VARCHAR(255), 
	description TEXT, 
	metadata JSONB NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_wallet_ledger_entries PRIMARY KEY (id), 
	CONSTRAINT uq_wallet_ledger_external_reference UNIQUE (external_reference), 
	CONSTRAINT fk_wallet_ledger_entries_wallet_id_wallets FOREIGN KEY(wallet_id) REFERENCES wallets (id) ON DELETE CASCADE, 
	CONSTRAINT uq_wallet_ledger_entries_external_reference UNIQUE (external_reference)
)

""",
    r"""CREATE TABLE episodes (
	season_id UUID NOT NULL, 
	episode_number INTEGER NOT NULL, 
	title VARCHAR(255), 
	synopsis TEXT, 
	runtime_minutes INTEGER, 
	air_date TIMESTAMP WITH TIME ZONE, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_episodes PRIMARY KEY (id), 
	CONSTRAINT uq_episodes_season_number UNIQUE (season_id, episode_number), 
	CONSTRAINT fk_episodes_season_id_seasons FOREIGN KEY(season_id) REFERENCES seasons (id) ON DELETE CASCADE
)

""",
    r"""CREATE TABLE payments (
	order_id UUID NOT NULL, 
	payment_attempt_id UUID NOT NULL, 
	provider VARCHAR(32) NOT NULL, 
	provider_reference VARCHAR(255) NOT NULL, 
	amount_irr NUMERIC(18, 2) NOT NULL, 
	status VARCHAR(32) NOT NULL, 
	paid_at TIMESTAMP WITH TIME ZONE, 
	metadata JSONB NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_payments PRIMARY KEY (id), 
	CONSTRAINT uq_payments_provider_reference UNIQUE (provider, provider_reference), 
	CONSTRAINT fk_payments_order_id_orders FOREIGN KEY(order_id) REFERENCES orders (id) ON DELETE RESTRICT, 
	CONSTRAINT fk_payments_payment_attempt_id_payment_attempts FOREIGN KEY(payment_attempt_id) REFERENCES payment_attempts (id) ON DELETE RESTRICT
)

""",
    r"""CREATE TABLE referral_rewards (
	referral_id UUID NOT NULL, 
	reward_type VARCHAR(64) NOT NULL, 
	amount_irr NUMERIC(18, 2), 
	status VARCHAR(32) NOT NULL, 
	granted_at TIMESTAMP WITH TIME ZONE, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_referral_rewards PRIMARY KEY (id), 
	CONSTRAINT uq_referral_reward_type UNIQUE (referral_id, reward_type), 
	CONSTRAINT fk_referral_rewards_referral_id_referrals FOREIGN KEY(referral_id) REFERENCES referrals (id) ON DELETE CASCADE
)

""",
    r"""CREATE TABLE refunds (
	payment_id UUID NOT NULL, 
	provider VARCHAR(32) NOT NULL, 
	provider_reference VARCHAR(255) NOT NULL, 
	amount_irr NUMERIC(18, 2) NOT NULL, 
	status VARCHAR(32) NOT NULL, 
	reason TEXT, 
	metadata JSONB NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_refunds PRIMARY KEY (id), 
	CONSTRAINT uq_refunds_provider_reference UNIQUE (provider, provider_reference), 
	CONSTRAINT fk_refunds_payment_id_payments FOREIGN KEY(payment_id) REFERENCES payments (id) ON DELETE RESTRICT
)

""",
    r"""CREATE TABLE releases (
	title_id UUID, 
	episode_id UUID, 
	label VARCHAR(160), 
	quality VARCHAR(32) NOT NULL, 
	width INTEGER, 
	height INTEGER, 
	codec_video VARCHAR(64), 
	codec_audio VARCHAR(64), 
	container VARCHAR(16), 
	fps NUMERIC(7, 3), 
	bitrate_kbps INTEGER, 
	size_bytes BIGINT, 
	source VARCHAR(64), 
	dynamic_range VARCHAR(32), 
	language VARCHAR(32) NOT NULL, 
	subtitle_type VARCHAR(32) NOT NULL, 
	status VARCHAR(32) NOT NULL, 
	priority INTEGER NOT NULL, 
	is_featured BOOLEAN NOT NULL, 
	technical_metadata JSONB NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_releases PRIMARY KEY (id), 
	CONSTRAINT ck_releases_single_parent CHECK ((title_id IS NOT NULL AND episode_id IS NULL) OR (title_id IS NULL AND episode_id IS NOT NULL)), 
	CONSTRAINT fk_releases_title_id_titles FOREIGN KEY(title_id) REFERENCES titles (id) ON DELETE CASCADE, 
	CONSTRAINT fk_releases_episode_id_episodes FOREIGN KEY(episode_id) REFERENCES episodes (id) ON DELETE CASCADE
)

""",
    r"""CREATE TABLE watch_history (
	user_id UUID NOT NULL, 
	title_id UUID NOT NULL, 
	episode_id UUID, 
	progress_seconds INTEGER NOT NULL, 
	completed BOOLEAN NOT NULL, 
	last_watched_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_watch_history PRIMARY KEY (id), 
	CONSTRAINT uq_watch_history_user_title UNIQUE (user_id, title_id), 
	CONSTRAINT fk_watch_history_user_id_users FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE, 
	CONSTRAINT fk_watch_history_title_id_titles FOREIGN KEY(title_id) REFERENCES titles (id) ON DELETE CASCADE, 
	CONSTRAINT fk_watch_history_episode_id_episodes FOREIGN KEY(episode_id) REFERENCES episodes (id) ON DELETE SET NULL
)

""",
    r"""CREATE TABLE access_policies (
	release_id UUID NOT NULL, 
	access_type VARCHAR(32) NOT NULL, 
	requires_subscription BOOLEAN NOT NULL, 
	minimum_plan_rank INTEGER NOT NULL, 
	active BOOLEAN NOT NULL, 
	metadata JSONB NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_access_policies PRIMARY KEY (id), 
	CONSTRAINT uq_access_policies_release UNIQUE (release_id), 
	CONSTRAINT fk_access_policies_release_id_releases FOREIGN KEY(release_id) REFERENCES releases (id) ON DELETE CASCADE
)

""",
    r"""CREATE TABLE analytics_events (
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	event_type VARCHAR(64) NOT NULL, 
	user_id UUID, 
	title_id UUID, 
	release_id UUID, 
	session_id VARCHAR(128), 
	payload JSONB NOT NULL, 
	id UUID NOT NULL, 
	CONSTRAINT pk_analytics_events PRIMARY KEY (id), 
	CONSTRAINT fk_analytics_events_user_id_users FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE SET NULL, 
	CONSTRAINT fk_analytics_events_title_id_titles FOREIGN KEY(title_id) REFERENCES titles (id) ON DELETE SET NULL, 
	CONSTRAINT fk_analytics_events_release_id_releases FOREIGN KEY(release_id) REFERENCES releases (id) ON DELETE SET NULL
)

""",
    r"""CREATE TABLE download_history (
	user_id UUID NOT NULL, 
	release_id UUID NOT NULL, 
	telegram_message_id BIGINT, 
	status VARCHAR(32) NOT NULL, 
	error_code VARCHAR(64), 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_download_history PRIMARY KEY (id), 
	CONSTRAINT fk_download_history_user_id_users FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE, 
	CONSTRAINT fk_download_history_release_id_releases FOREIGN KEY(release_id) REFERENCES releases (id) ON DELETE RESTRICT
)

""",
    r"""CREATE TABLE membership_rules (
	membership_channel_id UUID NOT NULL, 
	title_id UUID, 
	release_id UUID, 
	active BOOLEAN NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_membership_rules PRIMARY KEY (id), 
	CONSTRAINT ck_membership_rules_target CHECK ((title_id IS NOT NULL) OR (release_id IS NOT NULL)), 
	CONSTRAINT uq_membership_rule_target UNIQUE (membership_channel_id, title_id, release_id), 
	CONSTRAINT fk_membership_rules_membership_channel_id_membership_channels FOREIGN KEY(membership_channel_id) REFERENCES membership_channels (id) ON DELETE CASCADE, 
	CONSTRAINT fk_membership_rules_title_id_titles FOREIGN KEY(title_id) REFERENCES titles (id) ON DELETE CASCADE, 
	CONSTRAINT fk_membership_rules_release_id_releases FOREIGN KEY(release_id) REFERENCES releases (id) ON DELETE CASCADE
)

""",
    r"""CREATE TABLE release_audio_tracks (
	release_id UUID NOT NULL, 
	language VARCHAR(32) NOT NULL, 
	label VARCHAR(100), 
	codec VARCHAR(64), 
	channels VARCHAR(16), 
	track_index INTEGER NOT NULL, 
	is_default BOOLEAN NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_release_audio_tracks PRIMARY KEY (id), 
	CONSTRAINT uq_release_audio_track UNIQUE (release_id, language, track_index), 
	CONSTRAINT fk_release_audio_tracks_release_id_releases FOREIGN KEY(release_id) REFERENCES releases (id) ON DELETE CASCADE
)

""",
    r"""CREATE TABLE release_files (
	release_id UUID NOT NULL, 
	storage_file_id UUID NOT NULL, 
	is_primary BOOLEAN NOT NULL, 
	active BOOLEAN NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_release_files PRIMARY KEY (id), 
	CONSTRAINT uq_release_file_link UNIQUE (release_id, storage_file_id), 
	CONSTRAINT fk_release_files_release_id_releases FOREIGN KEY(release_id) REFERENCES releases (id) ON DELETE CASCADE, 
	CONSTRAINT fk_release_files_storage_file_id_storage_files FOREIGN KEY(storage_file_id) REFERENCES storage_files (id) ON DELETE RESTRICT
)

""",
    r"""CREATE TABLE release_subtitle_tracks (
	release_id UUID NOT NULL, 
	language VARCHAR(32) NOT NULL, 
	label VARCHAR(100), 
	format VARCHAR(16), 
	track_index INTEGER NOT NULL, 
	is_default BOOLEAN NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_release_subtitle_tracks PRIMARY KEY (id), 
	CONSTRAINT uq_release_subtitle_track UNIQUE (release_id, language, track_index), 
	CONSTRAINT fk_release_subtitle_tracks_release_id_releases FOREIGN KEY(release_id) REFERENCES releases (id) ON DELETE CASCADE
)

""",
    r"""CREATE TABLE transcode_jobs (
	source_file_id UUID NOT NULL, 
	target_release_id UUID, 
	target_quality VARCHAR(32) NOT NULL, 
	status VARCHAR(32) NOT NULL, 
	progress INTEGER NOT NULL, 
	error_message TEXT, 
	metadata JSONB NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_transcode_jobs PRIMARY KEY (id), 
	CONSTRAINT fk_transcode_jobs_source_file_id_storage_files FOREIGN KEY(source_file_id) REFERENCES storage_files (id) ON DELETE RESTRICT, 
	CONSTRAINT fk_transcode_jobs_target_release_id_releases FOREIGN KEY(target_release_id) REFERENCES releases (id) ON DELETE SET NULL
)

""",
    r"""CREATE INDEX ix_collections_parent_id ON collections (parent_id)""",
    r"""CREATE INDEX ix_people_name_en ON people (name_en)""",
    r"""CREATE INDEX ix_plans_active_sort ON plans (active, sort_order)""",
    r"""CREATE INDEX ix_titles_imdb_rating ON titles (imdb_rating)""",
    r"""CREATE INDEX ix_titles_status ON titles (status)""",
    r"""CREATE INDEX ix_titles_release_year ON titles (release_year)""",
    r"""CREATE INDEX ix_titles_kind ON titles (kind)""",
    r"""CREATE INDEX ix_users_status ON users (status)""",
    r"""CREATE INDEX ix_users_last_seen_at ON users (last_seen_at)""",
    r"""CREATE INDEX ix_audit_logs_created ON audit_logs (created_at)""",
    r"""CREATE INDEX ix_orders_user_status ON orders (user_id, status)""",
    r"""CREATE INDEX ix_storage_files_status ON storage_files (status)""",
    r"""CREATE INDEX ix_storage_files_message ON storage_files (chat_id, message_id)""",
    r"""CREATE INDEX ix_subscriptions_user_status ON subscriptions (user_id, status)""",
    r"""CREATE INDEX ix_subscriptions_expires_at ON subscriptions (expires_at)""",
    r"""CREATE INDEX ix_notification_jobs_due ON notification_jobs (status, run_at)""",
    r"""CREATE INDEX ix_payment_attempt_order_status ON payment_attempts (order_id, status)""",
    r"""CREATE INDEX ix_wallet_ledger_wallet_created ON wallet_ledger_entries (wallet_id, created_at)""",
    r"""CREATE INDEX ix_payments_order_status ON payments (order_id, status)""",
    r"""CREATE INDEX ix_releases_title_status ON releases (title_id, status)""",
    r"""CREATE INDEX ix_releases_episode_status ON releases (episode_id, status)""",
    r"""CREATE INDEX ix_releases_lookup ON releases (quality, language, subtitle_type, status)""",
    r"""CREATE INDEX ix_watch_history_user_updated ON watch_history (user_id, updated_at)""",
    r"""CREATE INDEX ix_analytics_events_user_created ON analytics_events (user_id, created_at)""",
    r"""CREATE INDEX ix_analytics_events_type_created ON analytics_events (event_type, created_at)""",
    r"""CREATE INDEX ix_download_history_release_created ON download_history (release_id, created_at)""",
    r"""CREATE INDEX ix_download_history_user_created ON download_history (user_id, created_at)""",
    r"""CREATE INDEX ix_transcode_jobs_source_file ON transcode_jobs (source_file_id)""",
    r"""CREATE INDEX ix_transcode_jobs_status ON transcode_jobs (status)""",
]

TABLES = ['collections', 'countries', 'genres', 'membership_channels', 'notification_templates', 'people', 'permissions', 'plans', 'roles', 'storage_providers', 'titles', 'users', 'audit_logs', 'favorites', 'orders', 'referral_codes', 'role_permissions', 'series', 'storage_files', 'subscriptions', 'title_collections', 'title_countries', 'title_genres', 'title_people', 'user_roles', 'wallets', 'notification_jobs', 'payment_attempts', 'referrals', 'seasons', 'wallet_ledger_entries', 'episodes', 'payments', 'referral_rewards', 'refunds', 'releases', 'watch_history', 'access_policies', 'analytics_events', 'download_history', 'membership_rules', 'release_audio_tracks', 'release_files', 'release_subtitle_tracks', 'transcode_jobs']

def upgrade() -> None:
    for statement in INITIAL_SQL:
        op.execute(statement)

def downgrade() -> None:
    for table_name in reversed(TABLES):
        op.execute(f"DROP TABLE IF EXISTS {table_name} CASCADE")
