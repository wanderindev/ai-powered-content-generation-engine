-- Auto-generated for migration 0002 downgrade: recreate the Flask-era
-- orphan objects (structure only; original data is not recoverable).
-- Extracted verbatim from 0000_baseline_schema.sql.

CREATE TYPE public.agent_type AS ENUM (
    'CONTENT_MANAGER',
    'RESEARCHER',
    'WRITER',
    'EDITOR',
    'SOCIAL_MEDIA',
    'TRANSLATOR',
    'MEDIA_MANAGER',
    'OUTLINER',
    'SUMMARIZER'
);

CREATE TYPE public.instagram_media_type AS ENUM (
    'SQUARE',
    'PORTRAIT',
    'LANDSCAPE',
    'STORY',
    'REEL'
);

CREATE TYPE public.platform_type AS ENUM (
    'INSTAGRAM'
);

CREATE TYPE public.post_type AS ENUM (
    'FEED',
    'STORY'
);

CREATE TYPE public.provider_type AS ENUM (
    'OPENAI',
    'ANTHROPIC'
);

CREATE TABLE public.agents (
    id integer NOT NULL,
    name character varying(100) NOT NULL,
    type public.agent_type NOT NULL,
    description text,
    model_id integer NOT NULL,
    temperature double precision,
    max_tokens integer NOT NULL,
    is_active boolean DEFAULT true NOT NULL,
    created_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone NOT NULL,
    thinking_enabled boolean DEFAULT false NOT NULL,
    thinking_budget integer,
    extended_output boolean DEFAULT false NOT NULL
);

CREATE SEQUENCE public.agents_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

CREATE TABLE public.ai_models (
    id integer NOT NULL,
    name character varying(100) NOT NULL,
    provider public.provider_type NOT NULL,
    model_id character varying(50) NOT NULL,
    description text,
    is_active boolean DEFAULT true NOT NULL,
    input_rate numeric(10,2),
    batch_input_rate numeric(10,2),
    output_rate numeric(10,2),
    batch_output_rate numeric(10,2),
    created_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone NOT NULL
);

CREATE SEQUENCE public.ai_models_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

CREATE TABLE public.api_usage (
    id integer NOT NULL,
    provider public.provider_type NOT NULL,
    model_id character varying(50) NOT NULL,
    input_tokens integer NOT NULL,
    output_tokens integer NOT NULL,
    cost double precision NOT NULL,
    "timestamp" timestamp with time zone NOT NULL,
    created_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone NOT NULL
);

CREATE SEQUENCE public.api_usage_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

CREATE TABLE public.approved_languages (
    id integer NOT NULL,
    code character varying(5) NOT NULL,
    name character varying(50) NOT NULL,
    is_active boolean DEFAULT true NOT NULL,
    is_default boolean DEFAULT false NOT NULL,
    created_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone NOT NULL,
    CONSTRAINT ck_default_must_be_active CHECK (
CASE
    WHEN is_default THEN is_active
    ELSE true
END)
);

CREATE SEQUENCE public.approved_languages_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

CREATE TABLE public.hashtag_groups (
    id integer NOT NULL,
    name character varying(100) NOT NULL,
    hashtags character varying(100)[] DEFAULT ARRAY[]::character varying[] NOT NULL,
    description text NOT NULL,
    is_core boolean DEFAULT false NOT NULL,
    created_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone NOT NULL
);

CREATE SEQUENCE public.hashtag_groups_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

CREATE TABLE public.social_media_accounts (
    id integer NOT NULL,
    platform public.platform_type NOT NULL,
    username character varying(100) NOT NULL,
    account_id character varying(100) NOT NULL,
    is_active boolean DEFAULT true NOT NULL,
    credentials json NOT NULL,
    created_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone NOT NULL
);

CREATE SEQUENCE public.social_media_accounts_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

CREATE TABLE public.social_media_post_media (
    post_id integer NOT NULL,
    media_id integer NOT NULL,
    "position" integer NOT NULL
);

CREATE TABLE public.social_media_posts (
    id integer NOT NULL,
    article_id integer NOT NULL,
    account_id integer NOT NULL,
    content text NOT NULL,
    hashtags character varying(100)[] DEFAULT ARRAY[]::character varying[] NOT NULL,
    status public.content_status_type DEFAULT 'PENDING'::public.content_status_type NOT NULL,
    approved_by_id integer,
    approved_at timestamp with time zone,
    scheduled_for timestamp with time zone,
    posted_at timestamp with time zone,
    created_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone NOT NULL,
    generation_started_at timestamp with time zone,
    last_generation_error text,
    model_id integer,
    post_type public.post_type DEFAULT 'FEED'::public.post_type NOT NULL,
    is_highlight boolean DEFAULT false NOT NULL
);

CREATE SEQUENCE public.social_media_posts_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

CREATE TABLE public.translations (
    id integer NOT NULL,
    entity_type character varying(50) NOT NULL,
    entity_id integer NOT NULL,
    field character varying(50) NOT NULL,
    language character varying(5) NOT NULL,
    content text NOT NULL,
    is_generated boolean DEFAULT false NOT NULL,
    generated_at timestamp with time zone,
    generated_by_id integer,
    approved_by_id integer,
    approved_at timestamp with time zone,
    created_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone NOT NULL,
    generation_started_at timestamp with time zone,
    last_generation_error text,
    model_id integer
);

CREATE SEQUENCE public.translations_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER TABLE ONLY public.agents ALTER COLUMN id SET DEFAULT nextval('public.agents_id_seq'::regclass);

ALTER TABLE ONLY public.ai_models ALTER COLUMN id SET DEFAULT nextval('public.ai_models_id_seq'::regclass);

ALTER TABLE ONLY public.api_usage ALTER COLUMN id SET DEFAULT nextval('public.api_usage_id_seq'::regclass);

ALTER TABLE ONLY public.approved_languages ALTER COLUMN id SET DEFAULT nextval('public.approved_languages_id_seq'::regclass);

ALTER TABLE ONLY public.hashtag_groups ALTER COLUMN id SET DEFAULT nextval('public.hashtag_groups_id_seq'::regclass);

ALTER TABLE ONLY public.social_media_accounts ALTER COLUMN id SET DEFAULT nextval('public.social_media_accounts_id_seq'::regclass);

ALTER TABLE ONLY public.social_media_posts ALTER COLUMN id SET DEFAULT nextval('public.social_media_posts_id_seq'::regclass);

ALTER TABLE ONLY public.translations ALTER COLUMN id SET DEFAULT nextval('public.translations_id_seq'::regclass);

ALTER TABLE ONLY public.agents
    ADD CONSTRAINT agents_name_key UNIQUE (name);

ALTER TABLE ONLY public.agents
    ADD CONSTRAINT agents_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.ai_models
    ADD CONSTRAINT ai_models_name_key UNIQUE (name);

ALTER TABLE ONLY public.ai_models
    ADD CONSTRAINT ai_models_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.api_usage
    ADD CONSTRAINT api_usage_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.approved_languages
    ADD CONSTRAINT approved_languages_code_key UNIQUE (code);

ALTER TABLE ONLY public.approved_languages
    ADD CONSTRAINT approved_languages_name_key UNIQUE (name);

ALTER TABLE ONLY public.approved_languages
    ADD CONSTRAINT approved_languages_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.hashtag_groups
    ADD CONSTRAINT hashtag_groups_name_key UNIQUE (name);

ALTER TABLE ONLY public.hashtag_groups
    ADD CONSTRAINT hashtag_groups_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.social_media_accounts
    ADD CONSTRAINT social_media_accounts_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.social_media_post_media
    ADD CONSTRAINT social_media_post_media_pkey PRIMARY KEY (post_id, media_id);

ALTER TABLE ONLY public.social_media_posts
    ADD CONSTRAINT social_media_posts_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.translations
    ADD CONSTRAINT translations_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.social_media_accounts
    ADD CONSTRAINT uq_social_media_accounts_platform_username UNIQUE (platform, username);

ALTER TABLE ONLY public.translations
    ADD CONSTRAINT uq_translation_entity_field_lang UNIQUE (entity_type, entity_id, field, language);

CREATE INDEX idx_agent_active ON public.agents USING btree (is_active);

CREATE INDEX idx_agent_type ON public.agents USING btree (type);

CREATE INDEX idx_aimodel_active ON public.ai_models USING btree (is_active);

CREATE INDEX idx_aimodel_provider ON public.ai_models USING btree (provider);

CREATE INDEX idx_hashtag_group_core ON public.hashtag_groups USING btree (is_core);

CREATE INDEX idx_hashtag_group_name ON public.hashtag_groups USING btree (name);

CREATE INDEX idx_language_active ON public.approved_languages USING btree (is_active);

CREATE INDEX idx_language_code ON public.approved_languages USING btree (code);

CREATE INDEX idx_language_default ON public.approved_languages USING btree (is_default);

CREATE INDEX idx_social_media_account_active ON public.social_media_accounts USING btree (is_active);

CREATE INDEX idx_social_media_account_platform ON public.social_media_accounts USING btree (platform);

CREATE UNIQUE INDEX idx_social_media_post_media_position ON public.social_media_post_media USING btree (post_id, "position");

CREATE INDEX idx_social_media_post_media_post ON public.social_media_post_media USING btree (post_id);

CREATE INDEX idx_social_media_post_posted ON public.social_media_posts USING btree (posted_at);

CREATE INDEX idx_social_media_post_scheduled ON public.social_media_posts USING btree (scheduled_for);

CREATE INDEX idx_social_media_post_status ON public.social_media_posts USING btree (status);

CREATE INDEX idx_translation_entity ON public.translations USING btree (entity_type, entity_id);

CREATE INDEX idx_translation_generated ON public.translations USING btree (is_generated);

CREATE INDEX idx_usage_provider_model ON public.api_usage USING btree (provider, model_id);

CREATE INDEX idx_usage_timestamp ON public.api_usage USING btree ("timestamp");

CREATE INDEX ix_agents_model_id ON public.agents USING btree (model_id);

CREATE INDEX ix_social_media_posts_account_id ON public.social_media_posts USING btree (account_id);

CREATE INDEX ix_social_media_posts_article_id ON public.social_media_posts USING btree (article_id);

CREATE INDEX ix_social_media_posts_model_id ON public.social_media_posts USING btree (model_id);

CREATE INDEX ix_translations_language ON public.translations USING btree (language);

CREATE INDEX ix_translations_model_id ON public.translations USING btree (model_id);

ALTER TABLE ONLY public.agents
    ADD CONSTRAINT agents_model_id_fkey FOREIGN KEY (model_id) REFERENCES public.ai_models(id) ON DELETE RESTRICT;

ALTER TABLE ONLY public.translations
    ADD CONSTRAINT fk_translations_model_id FOREIGN KEY (model_id) REFERENCES public.ai_models(id) ON DELETE SET NULL;

ALTER TABLE ONLY public.social_media_post_media
    ADD CONSTRAINT social_media_post_media_media_id_fkey FOREIGN KEY (media_id) REFERENCES public.media(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.social_media_post_media
    ADD CONSTRAINT social_media_post_media_post_id_fkey FOREIGN KEY (post_id) REFERENCES public.social_media_posts(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.social_media_posts
    ADD CONSTRAINT social_media_posts_account_id_fkey FOREIGN KEY (account_id) REFERENCES public.social_media_accounts(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.social_media_posts
    ADD CONSTRAINT social_media_posts_approved_by_id_fkey FOREIGN KEY (approved_by_id) REFERENCES public.users(id) ON DELETE SET NULL;

ALTER TABLE ONLY public.social_media_posts
    ADD CONSTRAINT social_media_posts_article_id_fkey FOREIGN KEY (article_id) REFERENCES public.articles(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.social_media_posts
    ADD CONSTRAINT social_media_posts_model_id_fkey FOREIGN KEY (model_id) REFERENCES public.ai_models(id) ON DELETE SET NULL;

ALTER TABLE ONLY public.translations
    ADD CONSTRAINT translations_approved_by_id_fkey FOREIGN KEY (approved_by_id) REFERENCES public.users(id) ON DELETE SET NULL;

ALTER TABLE ONLY public.translations
    ADD CONSTRAINT translations_generated_by_id_fkey FOREIGN KEY (generated_by_id) REFERENCES public.ai_models(id) ON DELETE SET NULL;

ALTER TABLE ONLY public.translations
    ADD CONSTRAINT translations_language_fkey FOREIGN KEY (language) REFERENCES public.approved_languages(code) ON DELETE CASCADE;

-- Media residue columns (recreate after their enum type exists)
ALTER TABLE public.media ADD COLUMN external_url character varying(512);
ALTER TABLE public.media ADD COLUMN instagram_media_type public.instagram_media_type;
