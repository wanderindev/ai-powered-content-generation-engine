--
-- PostgreSQL database dump
--


-- Dumped from database version 17.10
-- Dumped by pg_dump version 17.10


--
-- Name: agent_type; Type: TYPE; Schema: public; Owner: -
--

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


--
-- Name: content_status_type; Type: TYPE; Schema: public; Owner: -
--

CREATE TYPE public.content_status_type AS ENUM (
    'PENDING',
    'APPROVED',
    'REJECTED'
);


--
-- Name: instagram_media_type; Type: TYPE; Schema: public; Owner: -
--

CREATE TYPE public.instagram_media_type AS ENUM (
    'SQUARE',
    'PORTRAIT',
    'LANDSCAPE',
    'STORY',
    'REEL'
);


--
-- Name: media_source_type; Type: TYPE; Schema: public; Owner: -
--

CREATE TYPE public.media_source_type AS ENUM (
    'LOCAL',
    'YOUTUBE',
    'S3',
    'WIKIMEDIA',
    'GENERATED',
    'LOC'
);


--
-- Name: media_type_type; Type: TYPE; Schema: public; Owner: -
--

CREATE TYPE public.media_type_type AS ENUM (
    'IMAGE',
    'VIDEO',
    'DOCUMENT',
    'PDF',
    'SPREADSHEET',
    'OTHER'
);


--
-- Name: platform_type; Type: TYPE; Schema: public; Owner: -
--

CREATE TYPE public.platform_type AS ENUM (
    'INSTAGRAM'
);


--
-- Name: post_type; Type: TYPE; Schema: public; Owner: -
--

CREATE TYPE public.post_type AS ENUM (
    'FEED',
    'STORY'
);


--
-- Name: provider_type; Type: TYPE; Schema: public; Owner: -
--

CREATE TYPE public.provider_type AS ENUM (
    'OPENAI',
    'ANTHROPIC'
);




--
-- Name: admin_emails; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.admin_emails (
    id integer NOT NULL,
    email character varying(255) NOT NULL,
    name character varying(100),
    is_active boolean NOT NULL
);


--
-- Name: admin_emails_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.admin_emails_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: admin_emails_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.admin_emails_id_seq OWNED BY public.admin_emails.id;


--
-- Name: agents; Type: TABLE; Schema: public; Owner: -
--

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


--
-- Name: agents_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.agents_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: agents_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.agents_id_seq OWNED BY public.agents.id;


--
-- Name: ai_models; Type: TABLE; Schema: public; Owner: -
--

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


--
-- Name: ai_models_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.ai_models_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: ai_models_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.ai_models_id_seq OWNED BY public.ai_models.id;


--
-- Name: alembic_version; Type: TABLE; Schema: public; Owner: -
--



--
-- Name: api_usage; Type: TABLE; Schema: public; Owner: -
--

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


--
-- Name: api_usage_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.api_usage_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: api_usage_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.api_usage_id_seq OWNED BY public.api_usage.id;


--
-- Name: approved_languages; Type: TABLE; Schema: public; Owner: -
--

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


--
-- Name: TABLE approved_languages; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.approved_languages IS 'Supported languages for content translation';


--
-- Name: approved_languages_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.approved_languages_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: approved_languages_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.approved_languages_id_seq OWNED BY public.approved_languages.id;


--
-- Name: article_suggestions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.article_suggestions (
    id integer NOT NULL,
    category_id integer NOT NULL,
    title character varying(255) NOT NULL,
    main_topic text,
    sub_topics character varying(8000)[] DEFAULT ARRAY[]::character varying[],
    point_of_view text NOT NULL,
    status public.content_status_type DEFAULT 'PENDING'::public.content_status_type NOT NULL,
    approved_by_id integer,
    approved_at timestamp with time zone,
    created_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone NOT NULL,
    title_es character varying(255)
);


--
-- Name: TABLE article_suggestions; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.article_suggestions IS 'Article suggestions pending research and development';


--
-- Name: article_suggestions_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.article_suggestions_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: article_suggestions_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.article_suggestions_id_seq OWNED BY public.article_suggestions.id;


--
-- Name: article_tags; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.article_tags (
    article_id integer NOT NULL,
    tag_id integer NOT NULL
);


--
-- Name: articles; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.articles (
    id integer NOT NULL,
    research_id integer NOT NULL,
    category_id integer NOT NULL,
    feature_image_id integer,
    title character varying(255) NOT NULL,
    content text,
    excerpt text,
    ai_summary text,
    status public.content_status_type DEFAULT 'PENDING'::public.content_status_type NOT NULL,
    approved_by_id integer,
    approved_at timestamp with time zone,
    published_at timestamp with time zone,
    created_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone NOT NULL,
    series_order integer,
    series_parent_id integer,
    title_es character varying(255),
    content_es text,
    excerpt_es text,
    outline text,
    outline_status character varying(20)
);


--
-- Name: TABLE articles; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.articles IS 'Main article content with translations and relationships';


--
-- Name: COLUMN articles.series_order; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.articles.series_order IS 'Order in article series, null if standalone';


--
-- Name: COLUMN articles.series_parent_id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.articles.series_parent_id IS 'First article in the series';


--
-- Name: articles_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.articles_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: articles_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.articles_id_seq OWNED BY public.articles.id;


--
-- Name: attractions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.attractions (
    id integer NOT NULL,
    name character varying(200) NOT NULL,
    name_es character varying(200) NOT NULL,
    ticket_adult_tourist numeric(10,2) NOT NULL,
    ticket_senior_tourist numeric(10,2) NOT NULL,
    ticket_child_5_10_tourist numeric(10,2) NOT NULL,
    ticket_infant_tourist numeric(10,2) NOT NULL,
    ticket_adult_resident numeric(10,2) NOT NULL,
    ticket_senior_resident numeric(10,2) NOT NULL,
    ticket_child_5_10_resident numeric(10,2) NOT NULL,
    ticket_infant_resident numeric(10,2) NOT NULL,
    notes text,
    is_active boolean NOT NULL,
    min_age integer DEFAULT 0,
    ticket_child_10_17_tourist numeric(10,2) DEFAULT '0'::numeric NOT NULL,
    ticket_child_10_17_resident numeric(10,2) DEFAULT '0'::numeric NOT NULL
);


--
-- Name: attractions_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.attractions_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: attractions_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.attractions_id_seq OWNED BY public.attractions.id;


--
-- Name: booking_status_log; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.booking_status_log (
    id integer NOT NULL,
    booking_id integer,
    order_id integer,
    previous_status character varying(20),
    new_status character varying(20) NOT NULL,
    changed_by character varying(100),
    notes character varying(500),
    created_at timestamp without time zone DEFAULT now() NOT NULL
);


--
-- Name: booking_status_log_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.booking_status_log_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: booking_status_log_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.booking_status_log_id_seq OWNED BY public.booking_status_log.id;


--
-- Name: bookings; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.bookings (
    id integer NOT NULL,
    reference character varying(20) NOT NULL,
    order_id integer NOT NULL,
    tour_id integer NOT NULL,
    tour_date date NOT NULL,
    time_slot character varying(2) NOT NULL,
    preferred_time character varying(20),
    status character varying(20) NOT NULL,
    participants json NOT NULL,
    total_participants integer NOT NULL,
    total_residents integer NOT NULL,
    total_tourists integer NOT NULL,
    needs_transport boolean NOT NULL,
    transport_direction character varying(10),
    hotel_id integer,
    hotel_name_manual character varying(200),
    transport_quote_required boolean NOT NULL,
    service_total numeric(10,2) NOT NULL,
    senior_discount numeric(10,2) NOT NULL,
    tickets_total numeric(10,2) NOT NULL,
    transport_total numeric(10,2) NOT NULL,
    grand_total numeric(10,2) NOT NULL,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    updated_at timestamp without time zone DEFAULT now() NOT NULL
);


--
-- Name: bookings_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.bookings_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: bookings_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.bookings_id_seq OWNED BY public.bookings.id;


--
-- Name: categories; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.categories (
    id integer NOT NULL,
    taxonomy_id integer NOT NULL,
    name character varying(100) NOT NULL,
    description text NOT NULL,
    created_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone NOT NULL,
    is_long_form boolean DEFAULT true NOT NULL,
    name_es character varying(100),
    description_es text
);


--
-- Name: TABLE categories; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.categories IS 'Sub-categories within taxonomies';


--
-- Name: categories_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.categories_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: categories_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.categories_id_seq OWNED BY public.categories.id;


--
-- Name: contact_submissions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.contact_submissions (
    id integer NOT NULL,
    interest character varying(20) NOT NULL,
    first_name character varying(200) NOT NULL,
    last_name character varying(200) NOT NULL,
    email character varying(255) NOT NULL,
    message text,
    form_data jsonb,
    email_sent boolean DEFAULT false NOT NULL,
    email_error text,
    created_at timestamp without time zone DEFAULT now() NOT NULL
);


--
-- Name: contact_submissions_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.contact_submissions_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: contact_submissions_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.contact_submissions_id_seq OWNED BY public.contact_submissions.id;


--
-- Name: edu_material_files; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.edu_material_files (
    id integer NOT NULL,
    material_id integer NOT NULL,
    language character varying(5) NOT NULL,
    gamma_url character varying(500),
    file_url character varying(500) NOT NULL,
    file_format character varying(10) NOT NULL,
    version integer DEFAULT 1 NOT NULL,
    created_at timestamp without time zone DEFAULT now() NOT NULL
);


--
-- Name: edu_material_files_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.edu_material_files_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: edu_material_files_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.edu_material_files_id_seq OWNED BY public.edu_material_files.id;


--
-- Name: edu_materials; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.edu_materials (
    id integer NOT NULL,
    research_id integer,
    material_type character varying(20) NOT NULL,
    grade_band character varying(20),
    title character varying(255) NOT NULL,
    content text,
    content_es text,
    status public.content_status_type DEFAULT 'PENDING'::public.content_status_type NOT NULL,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    updated_at timestamp without time zone DEFAULT now() NOT NULL,
    blog_research_id integer,
    file_url character varying(500),
    thumbnail_url character varying(500)
);


--
-- Name: edu_materials_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.edu_materials_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: edu_materials_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.edu_materials_id_seq OWNED BY public.edu_materials.id;


--
-- Name: edu_research; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.edu_research (
    id integer NOT NULL,
    suggestion_id integer NOT NULL,
    content text NOT NULL,
    status public.content_status_type DEFAULT 'PENDING'::public.content_status_type NOT NULL,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    updated_at timestamp without time zone DEFAULT now() NOT NULL
);


--
-- Name: edu_research_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.edu_research_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: edu_research_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.edu_research_id_seq OWNED BY public.edu_research.id;


--
-- Name: edu_suggestions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.edu_suggestions (
    id integer NOT NULL,
    excursion_key character varying(100),
    title character varying(255) NOT NULL,
    description text,
    grade_bands character varying[],
    sub_topics character varying[],
    status public.content_status_type DEFAULT 'PENDING'::public.content_status_type NOT NULL,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    updated_at timestamp without time zone DEFAULT now() NOT NULL
);


--
-- Name: edu_suggestions_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.edu_suggestions_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: edu_suggestions_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.edu_suggestions_id_seq OWNED BY public.edu_suggestions.id;


--
-- Name: educators; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.educators (
    id integer NOT NULL,
    email character varying(255) NOT NULL,
    name character varying(255),
    institution character varying(255),
    status character varying(20) DEFAULT 'PENDING'::character varying NOT NULL,
    language character varying(5) DEFAULT 'es'::character varying NOT NULL,
    source character varying(50),
    confirm_token character varying(64),
    verify_code character varying(6),
    verify_code_expires_at timestamp without time zone,
    access_expires_at timestamp without time zone,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    confirmed_at timestamp without time zone,
    last_access_at timestamp without time zone,
    unsubscribed_at timestamp without time zone,
    mailing_list boolean DEFAULT false NOT NULL
);


--
-- Name: educators_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.educators_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: educators_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.educators_id_seq OWNED BY public.educators.id;


--
-- Name: hashtag_groups; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.hashtag_groups (
    id integer NOT NULL,
    name character varying(100) NOT NULL,
    hashtags character varying(100)[] DEFAULT ARRAY[]::character varying[] NOT NULL,
    description text NOT NULL,
    is_core boolean DEFAULT false NOT NULL,
    created_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone NOT NULL
);


--
-- Name: TABLE hashtag_groups; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.hashtag_groups IS 'Predefined groups of hashtags for social media posts';


--
-- Name: COLUMN hashtag_groups.is_core; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.hashtag_groups.is_core IS 'Whether this is a core group that should be included in all posts';


--
-- Name: hashtag_groups_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.hashtag_groups_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: hashtag_groups_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.hashtag_groups_id_seq OWNED BY public.hashtag_groups.id;


--
-- Name: hotels; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.hotels (
    id integer NOT NULL,
    name character varying(200) NOT NULL,
    name_es character varying(200),
    address character varying(500),
    address_es character varying(500),
    zone_id integer NOT NULL,
    is_active boolean NOT NULL
);


--
-- Name: hotels_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.hotels_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: hotels_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.hotels_id_seq OWNED BY public.hotels.id;


--
-- Name: magic_links; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.magic_links (
    id integer NOT NULL,
    token character varying(64) NOT NULL,
    order_id integer,
    booking_id integer,
    admin_email character varying(255),
    action character varying(20) NOT NULL,
    expires_at timestamp without time zone NOT NULL,
    used_at timestamp without time zone,
    created_at timestamp without time zone DEFAULT now() NOT NULL
);


--
-- Name: magic_links_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.magic_links_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: magic_links_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.magic_links_id_seq OWNED BY public.magic_links.id;


--
-- Name: media; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.media (
    id integer NOT NULL,
    filename character varying(255) NOT NULL,
    original_filename character varying(255) NOT NULL,
    file_path character varying(512) NOT NULL,
    file_size integer NOT NULL,
    mime_type character varying(127) NOT NULL,
    media_type public.media_type_type NOT NULL,
    source public.media_source_type NOT NULL,
    title character varying(255),
    caption text,
    alt_text character varying(255),
    external_url character varying(512),
    created_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone NOT NULL,
    width integer,
    height integer,
    instagram_media_type public.instagram_media_type,
    license character varying(100),
    license_url character varying(512),
    attribution text,
    source_url character varying(512),
    commons_id character varying(255),
    thumbnail_path character varying(512),
    search_terms text
);


--
-- Name: TABLE media; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.media IS 'Media files with metadata, licensing, and relationships';


--
-- Name: COLUMN media.license; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.media.license IS 'License type (e.g., CC BY-SA 4.0)';


--
-- Name: COLUMN media.license_url; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.media.license_url IS 'URL to license details';


--
-- Name: COLUMN media.attribution; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.media.attribution IS 'Required attribution text';


--
-- Name: COLUMN media.source_url; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.media.source_url IS 'Original source URL';


--
-- Name: COLUMN media.commons_id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.media.commons_id IS 'Wikimedia Commons file identifier';


--
-- Name: media_candidates; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.media_candidates (
    id integer NOT NULL,
    source character varying(50) NOT NULL,
    external_id character varying(255) NOT NULL,
    external_url character varying(1024) NOT NULL,
    thumbnail_url character varying(1024),
    page_url character varying(1024),
    title character varying(500) NOT NULL,
    description text,
    author character varying(500),
    tags character varying(255)[],
    license character varying(100),
    license_url character varying(512),
    attribution text,
    width integer,
    height integer,
    mime_type character varying(50),
    file_size integer,
    relevance_score double precision,
    relevance_notes text,
    status character varying(20) DEFAULT 'PENDING'::character varying NOT NULL,
    review_notes text,
    reviewed_at timestamp with time zone,
    source_categories character varying(255)[],
    crawled_at timestamp with time zone,
    media_id integer,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: media_candidates_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.media_candidates_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: media_candidates_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.media_candidates_id_seq OWNED BY public.media_candidates.id;


--
-- Name: media_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.media_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: media_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.media_id_seq OWNED BY public.media.id;


--
-- Name: media_tags; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.media_tags (
    media_id integer NOT NULL,
    tag_id integer NOT NULL
);


--
-- Name: orders; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.orders (
    id integer NOT NULL,
    reference character varying(20) NOT NULL,
    customer_name character varying(200) NOT NULL,
    customer_email character varying(255) NOT NULL,
    customer_phone character varying(50) NOT NULL,
    customer_hotel character varying(200),
    customer_language character varying(2) NOT NULL,
    status character varying(20) NOT NULL,
    payment_method character varying(20),
    payment_link_sent_at timestamp without time zone,
    payment_link_expires_at timestamp without time zone,
    paid_at timestamp without time zone,
    paypal_invoice_id character varying(50),
    paypal_invoice_url character varying(500),
    reservation_expires_at timestamp without time zone,
    followup_1_sent_at timestamp without time zone,
    followup_2_sent_at timestamp without time zone,
    followup_3_sent_at timestamp without time zone,
    subtotal numeric(10,2) NOT NULL,
    grand_total numeric(10,2) NOT NULL,
    admin_notes text,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    updated_at timestamp without time zone DEFAULT now() NOT NULL
);


--
-- Name: orders_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.orders_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: orders_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.orders_id_seq OWNED BY public.orders.id;


--
-- Name: pricing_config; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.pricing_config (
    key character varying(50) NOT NULL,
    value character varying(255) NOT NULL,
    description character varying(255),
    updated_at timestamp without time zone DEFAULT now() NOT NULL
);


--
-- Name: research; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.research (
    id integer NOT NULL,
    suggestion_id integer NOT NULL,
    content text NOT NULL,
    status public.content_status_type DEFAULT 'PENDING'::public.content_status_type NOT NULL,
    approved_by_id integer,
    approved_at timestamp with time zone,
    created_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone NOT NULL,
    summary text
);


--
-- Name: TABLE research; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.research IS 'Research content for article suggestions';


--
-- Name: research_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.research_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: research_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.research_id_seq OWNED BY public.research.id;


--
-- Name: social_media_accounts; Type: TABLE; Schema: public; Owner: -
--

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


--
-- Name: TABLE social_media_accounts; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.social_media_accounts IS 'Social media platform accounts configuration';


--
-- Name: COLUMN social_media_accounts.credentials; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.social_media_accounts.credentials IS 'Encrypted credentials for the platform';


--
-- Name: social_media_accounts_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.social_media_accounts_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: social_media_accounts_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.social_media_accounts_id_seq OWNED BY public.social_media_accounts.id;


--
-- Name: social_media_post_media; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.social_media_post_media (
    post_id integer NOT NULL,
    media_id integer NOT NULL,
    "position" integer NOT NULL
);


--
-- Name: COLUMN social_media_post_media."position"; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.social_media_post_media."position" IS 'Order position in carousel, starting from 0';


--
-- Name: social_media_posts; Type: TABLE; Schema: public; Owner: -
--

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


--
-- Name: TABLE social_media_posts; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.social_media_posts IS 'Social media posts with scheduling and tracking';


--
-- Name: COLUMN social_media_posts.is_highlight; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.social_media_posts.is_highlight IS 'Whether this story is saved as a highlight';


--
-- Name: social_media_posts_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.social_media_posts_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: social_media_posts_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.social_media_posts_id_seq OWNED BY public.social_media_posts.id;


--
-- Name: tags; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.tags (
    id integer NOT NULL,
    name character varying(50) NOT NULL,
    status public.content_status_type DEFAULT 'PENDING'::public.content_status_type NOT NULL,
    approved_by_id integer,
    approved_at timestamp with time zone,
    created_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone NOT NULL,
    name_es character varying(255)
);


--
-- Name: TABLE tags; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.tags IS 'Content categorization tags with approval workflow';


--
-- Name: tags_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.tags_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: tags_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.tags_id_seq OWNED BY public.tags.id;


--
-- Name: taxonomies; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.taxonomies (
    id integer NOT NULL,
    name character varying(100) NOT NULL,
    description text NOT NULL,
    created_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone NOT NULL,
    name_es character varying(100),
    description_es text
);


--
-- Name: TABLE taxonomies; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.taxonomies IS 'Main content categorization hierarchy';


--
-- Name: taxonomies_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.taxonomies_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: taxonomies_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.taxonomies_id_seq OWNED BY public.taxonomies.id;


--
-- Name: tour_attractions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.tour_attractions (
    id integer NOT NULL,
    tour_id integer NOT NULL,
    attraction_id integer NOT NULL,
    is_optional boolean NOT NULL,
    visit_order integer NOT NULL
);


--
-- Name: tour_attractions_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.tour_attractions_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: tour_attractions_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.tour_attractions_id_seq OWNED BY public.tour_attractions.id;


--
-- Name: tours; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.tours (
    id integer NOT NULL,
    slug character varying(100) NOT NULL,
    name character varying(200) NOT NULL,
    name_es character varying(200) NOT NULL,
    description text,
    description_es text,
    duration_hours numeric(4,2) NOT NULL,
    max_group_size integer NOT NULL,
    zone_id integer NOT NULL,
    is_active boolean NOT NULL,
    display_order integer NOT NULL,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    updated_at timestamp without time zone DEFAULT now() NOT NULL,
    fixed_price_config jsonb
);


--
-- Name: tours_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.tours_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: tours_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.tours_id_seq OWNED BY public.tours.id;


--
-- Name: translations; Type: TABLE; Schema: public; Owner: -
--

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


--
-- Name: TABLE translations; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.translations IS 'Content translations with generation tracking';


--
-- Name: translations_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.translations_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: translations_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.translations_id_seq OWNED BY public.translations.id;


--
-- Name: users; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.users (
    id integer NOT NULL,
    email character varying(120) NOT NULL,
    full_name character varying(100) NOT NULL,
    password_hash character varying(200) NOT NULL,
    active boolean DEFAULT true NOT NULL,
    last_login_at timestamp with time zone,
    created_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone NOT NULL
);


--
-- Name: TABLE users; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.users IS 'Stores user authentication and profile information';


--
-- Name: users_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.users_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: users_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.users_id_seq OWNED BY public.users.id;


--
-- Name: zone_attraction_rates; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.zone_attraction_rates (
    id integer NOT NULL,
    zone_id integer NOT NULL,
    attraction_id integer NOT NULL,
    rate_small numeric(10,2) NOT NULL,
    rate_medium numeric(10,2) NOT NULL,
    rate_large numeric(10,2) NOT NULL
);


--
-- Name: zone_attraction_rates_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.zone_attraction_rates_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: zone_attraction_rates_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.zone_attraction_rates_id_seq OWNED BY public.zone_attraction_rates.id;


--
-- Name: zone_transport_rates; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.zone_transport_rates (
    id integer NOT NULL,
    from_zone_id integer NOT NULL,
    to_zone_id integer NOT NULL,
    rate_small_group numeric(10,2) NOT NULL,
    rate_large_group numeric(10,2) NOT NULL
);


--
-- Name: zone_transport_rates_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.zone_transport_rates_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: zone_transport_rates_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.zone_transport_rates_id_seq OWNED BY public.zone_transport_rates.id;


--
-- Name: zones; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.zones (
    id integer NOT NULL,
    name character varying(100) NOT NULL,
    name_es character varying(100) NOT NULL,
    description character varying(255),
    description_es character varying(255),
    display_order integer NOT NULL,
    is_active boolean NOT NULL
);


--
-- Name: zones_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.zones_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: zones_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.zones_id_seq OWNED BY public.zones.id;


--
-- Name: admin_emails id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.admin_emails ALTER COLUMN id SET DEFAULT nextval('public.admin_emails_id_seq'::regclass);


--
-- Name: agents id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agents ALTER COLUMN id SET DEFAULT nextval('public.agents_id_seq'::regclass);


--
-- Name: ai_models id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ai_models ALTER COLUMN id SET DEFAULT nextval('public.ai_models_id_seq'::regclass);


--
-- Name: api_usage id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.api_usage ALTER COLUMN id SET DEFAULT nextval('public.api_usage_id_seq'::regclass);


--
-- Name: approved_languages id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.approved_languages ALTER COLUMN id SET DEFAULT nextval('public.approved_languages_id_seq'::regclass);


--
-- Name: article_suggestions id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.article_suggestions ALTER COLUMN id SET DEFAULT nextval('public.article_suggestions_id_seq'::regclass);


--
-- Name: articles id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.articles ALTER COLUMN id SET DEFAULT nextval('public.articles_id_seq'::regclass);


--
-- Name: attractions id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.attractions ALTER COLUMN id SET DEFAULT nextval('public.attractions_id_seq'::regclass);


--
-- Name: booking_status_log id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.booking_status_log ALTER COLUMN id SET DEFAULT nextval('public.booking_status_log_id_seq'::regclass);


--
-- Name: bookings id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.bookings ALTER COLUMN id SET DEFAULT nextval('public.bookings_id_seq'::regclass);


--
-- Name: categories id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.categories ALTER COLUMN id SET DEFAULT nextval('public.categories_id_seq'::regclass);


--
-- Name: contact_submissions id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.contact_submissions ALTER COLUMN id SET DEFAULT nextval('public.contact_submissions_id_seq'::regclass);


--
-- Name: edu_material_files id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.edu_material_files ALTER COLUMN id SET DEFAULT nextval('public.edu_material_files_id_seq'::regclass);


--
-- Name: edu_materials id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.edu_materials ALTER COLUMN id SET DEFAULT nextval('public.edu_materials_id_seq'::regclass);


--
-- Name: edu_research id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.edu_research ALTER COLUMN id SET DEFAULT nextval('public.edu_research_id_seq'::regclass);


--
-- Name: edu_suggestions id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.edu_suggestions ALTER COLUMN id SET DEFAULT nextval('public.edu_suggestions_id_seq'::regclass);


--
-- Name: educators id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.educators ALTER COLUMN id SET DEFAULT nextval('public.educators_id_seq'::regclass);


--
-- Name: hashtag_groups id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.hashtag_groups ALTER COLUMN id SET DEFAULT nextval('public.hashtag_groups_id_seq'::regclass);


--
-- Name: hotels id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.hotels ALTER COLUMN id SET DEFAULT nextval('public.hotels_id_seq'::regclass);


--
-- Name: magic_links id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.magic_links ALTER COLUMN id SET DEFAULT nextval('public.magic_links_id_seq'::regclass);


--
-- Name: media id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.media ALTER COLUMN id SET DEFAULT nextval('public.media_id_seq'::regclass);


--
-- Name: media_candidates id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.media_candidates ALTER COLUMN id SET DEFAULT nextval('public.media_candidates_id_seq'::regclass);


--
-- Name: orders id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.orders ALTER COLUMN id SET DEFAULT nextval('public.orders_id_seq'::regclass);


--
-- Name: research id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.research ALTER COLUMN id SET DEFAULT nextval('public.research_id_seq'::regclass);


--
-- Name: social_media_accounts id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.social_media_accounts ALTER COLUMN id SET DEFAULT nextval('public.social_media_accounts_id_seq'::regclass);


--
-- Name: social_media_posts id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.social_media_posts ALTER COLUMN id SET DEFAULT nextval('public.social_media_posts_id_seq'::regclass);


--
-- Name: tags id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tags ALTER COLUMN id SET DEFAULT nextval('public.tags_id_seq'::regclass);


--
-- Name: taxonomies id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.taxonomies ALTER COLUMN id SET DEFAULT nextval('public.taxonomies_id_seq'::regclass);


--
-- Name: tour_attractions id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tour_attractions ALTER COLUMN id SET DEFAULT nextval('public.tour_attractions_id_seq'::regclass);


--
-- Name: tours id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tours ALTER COLUMN id SET DEFAULT nextval('public.tours_id_seq'::regclass);


--
-- Name: translations id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.translations ALTER COLUMN id SET DEFAULT nextval('public.translations_id_seq'::regclass);


--
-- Name: users id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users ALTER COLUMN id SET DEFAULT nextval('public.users_id_seq'::regclass);


--
-- Name: zone_attraction_rates id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.zone_attraction_rates ALTER COLUMN id SET DEFAULT nextval('public.zone_attraction_rates_id_seq'::regclass);


--
-- Name: zone_transport_rates id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.zone_transport_rates ALTER COLUMN id SET DEFAULT nextval('public.zone_transport_rates_id_seq'::regclass);


--
-- Name: zones id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.zones ALTER COLUMN id SET DEFAULT nextval('public.zones_id_seq'::regclass);


--
-- Name: admin_emails admin_emails_email_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.admin_emails
    ADD CONSTRAINT admin_emails_email_key UNIQUE (email);


--
-- Name: admin_emails admin_emails_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.admin_emails
    ADD CONSTRAINT admin_emails_pkey PRIMARY KEY (id);


--
-- Name: agents agents_name_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agents
    ADD CONSTRAINT agents_name_key UNIQUE (name);


--
-- Name: agents agents_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agents
    ADD CONSTRAINT agents_pkey PRIMARY KEY (id);


--
-- Name: ai_models ai_models_name_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ai_models
    ADD CONSTRAINT ai_models_name_key UNIQUE (name);


--
-- Name: ai_models ai_models_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ai_models
    ADD CONSTRAINT ai_models_pkey PRIMARY KEY (id);


--
-- Name: alembic_version alembic_version_pkc; Type: CONSTRAINT; Schema: public; Owner: -
--



--
-- Name: api_usage api_usage_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.api_usage
    ADD CONSTRAINT api_usage_pkey PRIMARY KEY (id);


--
-- Name: approved_languages approved_languages_code_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.approved_languages
    ADD CONSTRAINT approved_languages_code_key UNIQUE (code);


--
-- Name: approved_languages approved_languages_name_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.approved_languages
    ADD CONSTRAINT approved_languages_name_key UNIQUE (name);


--
-- Name: approved_languages approved_languages_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.approved_languages
    ADD CONSTRAINT approved_languages_pkey PRIMARY KEY (id);


--
-- Name: article_suggestions article_suggestions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.article_suggestions
    ADD CONSTRAINT article_suggestions_pkey PRIMARY KEY (id);


--
-- Name: article_tags article_tags_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.article_tags
    ADD CONSTRAINT article_tags_pkey PRIMARY KEY (article_id, tag_id);


--
-- Name: articles articles_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.articles
    ADD CONSTRAINT articles_pkey PRIMARY KEY (id);


--
-- Name: attractions attractions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.attractions
    ADD CONSTRAINT attractions_pkey PRIMARY KEY (id);


--
-- Name: booking_status_log booking_status_log_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.booking_status_log
    ADD CONSTRAINT booking_status_log_pkey PRIMARY KEY (id);


--
-- Name: bookings bookings_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.bookings
    ADD CONSTRAINT bookings_pkey PRIMARY KEY (id);


--
-- Name: bookings bookings_reference_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.bookings
    ADD CONSTRAINT bookings_reference_key UNIQUE (reference);


--
-- Name: categories categories_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.categories
    ADD CONSTRAINT categories_pkey PRIMARY KEY (id);


--
-- Name: contact_submissions contact_submissions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.contact_submissions
    ADD CONSTRAINT contact_submissions_pkey PRIMARY KEY (id);


--
-- Name: edu_material_files edu_material_files_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.edu_material_files
    ADD CONSTRAINT edu_material_files_pkey PRIMARY KEY (id);


--
-- Name: edu_materials edu_materials_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.edu_materials
    ADD CONSTRAINT edu_materials_pkey PRIMARY KEY (id);


--
-- Name: edu_research edu_research_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.edu_research
    ADD CONSTRAINT edu_research_pkey PRIMARY KEY (id);


--
-- Name: edu_suggestions edu_suggestions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.edu_suggestions
    ADD CONSTRAINT edu_suggestions_pkey PRIMARY KEY (id);


--
-- Name: educators educators_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.educators
    ADD CONSTRAINT educators_pkey PRIMARY KEY (id);


--
-- Name: hashtag_groups hashtag_groups_name_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.hashtag_groups
    ADD CONSTRAINT hashtag_groups_name_key UNIQUE (name);


--
-- Name: hashtag_groups hashtag_groups_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.hashtag_groups
    ADD CONSTRAINT hashtag_groups_pkey PRIMARY KEY (id);


--
-- Name: hotels hotels_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.hotels
    ADD CONSTRAINT hotels_pkey PRIMARY KEY (id);


--
-- Name: magic_links magic_links_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.magic_links
    ADD CONSTRAINT magic_links_pkey PRIMARY KEY (id);


--
-- Name: magic_links magic_links_token_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.magic_links
    ADD CONSTRAINT magic_links_token_key UNIQUE (token);


--
-- Name: media_candidates media_candidates_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.media_candidates
    ADD CONSTRAINT media_candidates_pkey PRIMARY KEY (id);


--
-- Name: media media_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.media
    ADD CONSTRAINT media_pkey PRIMARY KEY (id);


--
-- Name: media_tags media_tags_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.media_tags
    ADD CONSTRAINT media_tags_pkey PRIMARY KEY (media_id, tag_id);


--
-- Name: orders orders_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.orders
    ADD CONSTRAINT orders_pkey PRIMARY KEY (id);


--
-- Name: orders orders_reference_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.orders
    ADD CONSTRAINT orders_reference_key UNIQUE (reference);


--
-- Name: pricing_config pricing_config_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.pricing_config
    ADD CONSTRAINT pricing_config_pkey PRIMARY KEY (key);


--
-- Name: research research_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.research
    ADD CONSTRAINT research_pkey PRIMARY KEY (id);


--
-- Name: social_media_accounts social_media_accounts_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.social_media_accounts
    ADD CONSTRAINT social_media_accounts_pkey PRIMARY KEY (id);


--
-- Name: social_media_post_media social_media_post_media_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.social_media_post_media
    ADD CONSTRAINT social_media_post_media_pkey PRIMARY KEY (post_id, media_id);


--
-- Name: social_media_posts social_media_posts_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.social_media_posts
    ADD CONSTRAINT social_media_posts_pkey PRIMARY KEY (id);


--
-- Name: tags tags_name_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tags
    ADD CONSTRAINT tags_name_key UNIQUE (name);


--
-- Name: tags tags_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tags
    ADD CONSTRAINT tags_pkey PRIMARY KEY (id);


--
-- Name: taxonomies taxonomies_name_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.taxonomies
    ADD CONSTRAINT taxonomies_name_key UNIQUE (name);


--
-- Name: taxonomies taxonomies_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.taxonomies
    ADD CONSTRAINT taxonomies_pkey PRIMARY KEY (id);


--
-- Name: tour_attractions tour_attractions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tour_attractions
    ADD CONSTRAINT tour_attractions_pkey PRIMARY KEY (id);


--
-- Name: tours tours_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tours
    ADD CONSTRAINT tours_pkey PRIMARY KEY (id);


--
-- Name: tours tours_slug_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tours
    ADD CONSTRAINT tours_slug_key UNIQUE (slug);


--
-- Name: translations translations_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.translations
    ADD CONSTRAINT translations_pkey PRIMARY KEY (id);


--
-- Name: media_candidates uq_candidate_source_external_id; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.media_candidates
    ADD CONSTRAINT uq_candidate_source_external_id UNIQUE (source, external_id);


--
-- Name: categories uq_category_taxonomy_name; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.categories
    ADD CONSTRAINT uq_category_taxonomy_name UNIQUE (taxonomy_id, name);


--
-- Name: social_media_accounts uq_social_media_accounts_platform_username; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.social_media_accounts
    ADD CONSTRAINT uq_social_media_accounts_platform_username UNIQUE (platform, username);


--
-- Name: tour_attractions uq_tour_attractions; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tour_attractions
    ADD CONSTRAINT uq_tour_attractions UNIQUE (tour_id, attraction_id);


--
-- Name: translations uq_translation_entity_field_lang; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.translations
    ADD CONSTRAINT uq_translation_entity_field_lang UNIQUE (entity_type, entity_id, field, language);


--
-- Name: zone_attraction_rates uq_zone_attraction_rate; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.zone_attraction_rates
    ADD CONSTRAINT uq_zone_attraction_rate UNIQUE (zone_id, attraction_id);


--
-- Name: zone_transport_rates uq_zone_transport_rates; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.zone_transport_rates
    ADD CONSTRAINT uq_zone_transport_rates UNIQUE (from_zone_id, to_zone_id);


--
-- Name: users users_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_pkey PRIMARY KEY (id);


--
-- Name: zone_attraction_rates zone_attraction_rates_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.zone_attraction_rates
    ADD CONSTRAINT zone_attraction_rates_pkey PRIMARY KEY (id);


--
-- Name: zone_transport_rates zone_transport_rates_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.zone_transport_rates
    ADD CONSTRAINT zone_transport_rates_pkey PRIMARY KEY (id);


--
-- Name: zones zones_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.zones
    ADD CONSTRAINT zones_pkey PRIMARY KEY (id);


--
-- Name: idx_agent_active; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_agent_active ON public.agents USING btree (is_active);


--
-- Name: idx_agent_type; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_agent_type ON public.agents USING btree (type);


--
-- Name: idx_aimodel_active; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_aimodel_active ON public.ai_models USING btree (is_active);


--
-- Name: idx_aimodel_provider; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_aimodel_provider ON public.ai_models USING btree (provider);


--
-- Name: idx_article_published; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_article_published ON public.articles USING btree (published_at);


--
-- Name: idx_article_series; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_article_series ON public.articles USING btree (series_parent_id, series_order);


--
-- Name: idx_article_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_article_status ON public.articles USING btree (status);


--
-- Name: idx_article_suggestion_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_article_suggestion_status ON public.article_suggestions USING btree (status);


--
-- Name: idx_article_tags_article; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_article_tags_article ON public.article_tags USING btree (article_id);


--
-- Name: idx_article_tags_tag; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_article_tags_tag ON public.article_tags USING btree (tag_id);


--
-- Name: idx_category_name; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_category_name ON public.categories USING btree (name);


--
-- Name: idx_hashtag_group_core; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_hashtag_group_core ON public.hashtag_groups USING btree (is_core);


--
-- Name: idx_hashtag_group_name; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_hashtag_group_name ON public.hashtag_groups USING btree (name);


--
-- Name: idx_language_active; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_language_active ON public.approved_languages USING btree (is_active);


--
-- Name: idx_language_code; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_language_code ON public.approved_languages USING btree (code);


--
-- Name: idx_language_default; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_language_default ON public.approved_languages USING btree (is_default);


--
-- Name: idx_media_commons_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_media_commons_id ON public.media USING btree (commons_id);


--
-- Name: idx_media_mime_type; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_media_mime_type ON public.media USING btree (mime_type);


--
-- Name: idx_media_source; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_media_source ON public.media USING btree (source);


--
-- Name: idx_media_type; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_media_type ON public.media USING btree (media_type);


--
-- Name: idx_research_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_research_status ON public.research USING btree (status);


--
-- Name: idx_social_media_account_active; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_social_media_account_active ON public.social_media_accounts USING btree (is_active);


--
-- Name: idx_social_media_account_platform; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_social_media_account_platform ON public.social_media_accounts USING btree (platform);


--
-- Name: idx_social_media_post_media_position; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX idx_social_media_post_media_position ON public.social_media_post_media USING btree (post_id, "position");


--
-- Name: idx_social_media_post_media_post; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_social_media_post_media_post ON public.social_media_post_media USING btree (post_id);


--
-- Name: idx_social_media_post_posted; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_social_media_post_posted ON public.social_media_posts USING btree (posted_at);


--
-- Name: idx_social_media_post_scheduled; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_social_media_post_scheduled ON public.social_media_posts USING btree (scheduled_for);


--
-- Name: idx_social_media_post_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_social_media_post_status ON public.social_media_posts USING btree (status);


--
-- Name: idx_tag_name; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_tag_name ON public.tags USING btree (name);


--
-- Name: idx_tag_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_tag_status ON public.tags USING btree (status);


--
-- Name: idx_taxonomy_name; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_taxonomy_name ON public.taxonomies USING btree (name);


--
-- Name: idx_translation_entity; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_translation_entity ON public.translations USING btree (entity_type, entity_id);


--
-- Name: idx_translation_generated; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_translation_generated ON public.translations USING btree (is_generated);


--
-- Name: idx_usage_provider_model; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_usage_provider_model ON public.api_usage USING btree (provider, model_id);


--
-- Name: idx_usage_timestamp; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_usage_timestamp ON public.api_usage USING btree ("timestamp");


--
-- Name: idx_user_active; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_user_active ON public.users USING btree (active);


--
-- Name: idx_user_last_login; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_user_last_login ON public.users USING btree (last_login_at);


--
-- Name: ix_agents_model_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_agents_model_id ON public.agents USING btree (model_id);


--
-- Name: ix_article_suggestions_category_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_article_suggestions_category_id ON public.article_suggestions USING btree (category_id);


--
-- Name: ix_articles_category_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_articles_category_id ON public.articles USING btree (category_id);


--
-- Name: ix_articles_research_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_articles_research_id ON public.articles USING btree (research_id);


--
-- Name: ix_articles_series_parent_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_articles_series_parent_id ON public.articles USING btree (series_parent_id);


--
-- Name: ix_bookings_date_slot; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_bookings_date_slot ON public.bookings USING btree (tour_date, time_slot, status);


--
-- Name: ix_bookings_reference; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_bookings_reference ON public.bookings USING btree (reference);


--
-- Name: ix_bookings_tour_date; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_bookings_tour_date ON public.bookings USING btree (tour_date);


--
-- Name: ix_candidate_relevance; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_candidate_relevance ON public.media_candidates USING btree (relevance_score);


--
-- Name: ix_candidate_source; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_candidate_source ON public.media_candidates USING btree (source);


--
-- Name: ix_candidate_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_candidate_status ON public.media_candidates USING btree (status);


--
-- Name: ix_categories_taxonomy_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_categories_taxonomy_id ON public.categories USING btree (taxonomy_id);


--
-- Name: ix_edu_suggestions_excursion_key; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_edu_suggestions_excursion_key ON public.edu_suggestions USING btree (excursion_key);


--
-- Name: ix_educators_email; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX ix_educators_email ON public.educators USING btree (email);


--
-- Name: ix_hotels_name; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_hotels_name ON public.hotels USING btree (name);


--
-- Name: ix_hotels_zone_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_hotels_zone_id ON public.hotels USING btree (zone_id);


--
-- Name: ix_magic_links_token; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_magic_links_token ON public.magic_links USING btree (token);


--
-- Name: ix_orders_customer_email; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_orders_customer_email ON public.orders USING btree (customer_email);


--
-- Name: ix_orders_reference; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_orders_reference ON public.orders USING btree (reference);


--
-- Name: ix_orders_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_orders_status ON public.orders USING btree (status);


--
-- Name: ix_research_suggestion_id; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX ix_research_suggestion_id ON public.research USING btree (suggestion_id);


--
-- Name: ix_social_media_posts_account_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_social_media_posts_account_id ON public.social_media_posts USING btree (account_id);


--
-- Name: ix_social_media_posts_article_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_social_media_posts_article_id ON public.social_media_posts USING btree (article_id);


--
-- Name: ix_social_media_posts_model_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_social_media_posts_model_id ON public.social_media_posts USING btree (model_id);


--
-- Name: ix_tours_slug; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_tours_slug ON public.tours USING btree (slug);


--
-- Name: ix_translations_language; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_translations_language ON public.translations USING btree (language);


--
-- Name: ix_translations_model_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_translations_model_id ON public.translations USING btree (model_id);


--
-- Name: ix_users_email; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX ix_users_email ON public.users USING btree (email);


--
-- Name: agents agents_model_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agents
    ADD CONSTRAINT agents_model_id_fkey FOREIGN KEY (model_id) REFERENCES public.ai_models(id) ON DELETE RESTRICT;


--
-- Name: article_suggestions article_suggestions_approved_by_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.article_suggestions
    ADD CONSTRAINT article_suggestions_approved_by_id_fkey FOREIGN KEY (approved_by_id) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: article_suggestions article_suggestions_category_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.article_suggestions
    ADD CONSTRAINT article_suggestions_category_id_fkey FOREIGN KEY (category_id) REFERENCES public.categories(id) ON DELETE CASCADE;


--
-- Name: article_tags article_tags_article_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.article_tags
    ADD CONSTRAINT article_tags_article_id_fkey FOREIGN KEY (article_id) REFERENCES public.articles(id) ON DELETE CASCADE;


--
-- Name: article_tags article_tags_tag_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.article_tags
    ADD CONSTRAINT article_tags_tag_id_fkey FOREIGN KEY (tag_id) REFERENCES public.tags(id) ON DELETE CASCADE;


--
-- Name: articles articles_approved_by_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.articles
    ADD CONSTRAINT articles_approved_by_id_fkey FOREIGN KEY (approved_by_id) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: articles articles_category_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.articles
    ADD CONSTRAINT articles_category_id_fkey FOREIGN KEY (category_id) REFERENCES public.categories(id) ON DELETE RESTRICT;


--
-- Name: articles articles_feature_image_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.articles
    ADD CONSTRAINT articles_feature_image_id_fkey FOREIGN KEY (feature_image_id) REFERENCES public.media(id) ON DELETE SET NULL;


--
-- Name: articles articles_research_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.articles
    ADD CONSTRAINT articles_research_id_fkey FOREIGN KEY (research_id) REFERENCES public.research(id) ON DELETE RESTRICT;


--
-- Name: booking_status_log booking_status_log_booking_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.booking_status_log
    ADD CONSTRAINT booking_status_log_booking_id_fkey FOREIGN KEY (booking_id) REFERENCES public.bookings(id);


--
-- Name: booking_status_log booking_status_log_order_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.booking_status_log
    ADD CONSTRAINT booking_status_log_order_id_fkey FOREIGN KEY (order_id) REFERENCES public.orders(id);


--
-- Name: bookings bookings_hotel_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.bookings
    ADD CONSTRAINT bookings_hotel_id_fkey FOREIGN KEY (hotel_id) REFERENCES public.hotels(id);


--
-- Name: bookings bookings_order_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.bookings
    ADD CONSTRAINT bookings_order_id_fkey FOREIGN KEY (order_id) REFERENCES public.orders(id);


--
-- Name: bookings bookings_tour_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.bookings
    ADD CONSTRAINT bookings_tour_id_fkey FOREIGN KEY (tour_id) REFERENCES public.tours(id);


--
-- Name: categories categories_taxonomy_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.categories
    ADD CONSTRAINT categories_taxonomy_id_fkey FOREIGN KEY (taxonomy_id) REFERENCES public.taxonomies(id) ON DELETE CASCADE;


--
-- Name: edu_material_files edu_material_files_material_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.edu_material_files
    ADD CONSTRAINT edu_material_files_material_id_fkey FOREIGN KEY (material_id) REFERENCES public.edu_materials(id);


--
-- Name: edu_materials edu_materials_research_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.edu_materials
    ADD CONSTRAINT edu_materials_research_id_fkey FOREIGN KEY (research_id) REFERENCES public.edu_research(id);


--
-- Name: edu_research edu_research_suggestion_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.edu_research
    ADD CONSTRAINT edu_research_suggestion_id_fkey FOREIGN KEY (suggestion_id) REFERENCES public.edu_suggestions(id);


--
-- Name: articles fk_articles_series_parent; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.articles
    ADD CONSTRAINT fk_articles_series_parent FOREIGN KEY (series_parent_id) REFERENCES public.articles(id) ON DELETE SET NULL;


--
-- Name: edu_materials fk_edu_materials_blog_research; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.edu_materials
    ADD CONSTRAINT fk_edu_materials_blog_research FOREIGN KEY (blog_research_id) REFERENCES public.research(id);


--
-- Name: translations fk_translations_model_id; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.translations
    ADD CONSTRAINT fk_translations_model_id FOREIGN KEY (model_id) REFERENCES public.ai_models(id) ON DELETE SET NULL;


--
-- Name: hotels hotels_zone_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.hotels
    ADD CONSTRAINT hotels_zone_id_fkey FOREIGN KEY (zone_id) REFERENCES public.zones(id);


--
-- Name: magic_links magic_links_booking_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.magic_links
    ADD CONSTRAINT magic_links_booking_id_fkey FOREIGN KEY (booking_id) REFERENCES public.bookings(id);


--
-- Name: magic_links magic_links_order_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.magic_links
    ADD CONSTRAINT magic_links_order_id_fkey FOREIGN KEY (order_id) REFERENCES public.orders(id);


--
-- Name: media_tags media_tags_media_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.media_tags
    ADD CONSTRAINT media_tags_media_id_fkey FOREIGN KEY (media_id) REFERENCES public.media(id) ON DELETE CASCADE;


--
-- Name: media_tags media_tags_tag_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.media_tags
    ADD CONSTRAINT media_tags_tag_id_fkey FOREIGN KEY (tag_id) REFERENCES public.tags(id) ON DELETE CASCADE;


--
-- Name: research research_approved_by_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.research
    ADD CONSTRAINT research_approved_by_id_fkey FOREIGN KEY (approved_by_id) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: research research_suggestion_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.research
    ADD CONSTRAINT research_suggestion_id_fkey FOREIGN KEY (suggestion_id) REFERENCES public.article_suggestions(id) ON DELETE CASCADE;


--
-- Name: social_media_post_media social_media_post_media_media_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.social_media_post_media
    ADD CONSTRAINT social_media_post_media_media_id_fkey FOREIGN KEY (media_id) REFERENCES public.media(id) ON DELETE CASCADE;


--
-- Name: social_media_post_media social_media_post_media_post_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.social_media_post_media
    ADD CONSTRAINT social_media_post_media_post_id_fkey FOREIGN KEY (post_id) REFERENCES public.social_media_posts(id) ON DELETE CASCADE;


--
-- Name: social_media_posts social_media_posts_account_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.social_media_posts
    ADD CONSTRAINT social_media_posts_account_id_fkey FOREIGN KEY (account_id) REFERENCES public.social_media_accounts(id) ON DELETE CASCADE;


--
-- Name: social_media_posts social_media_posts_approved_by_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.social_media_posts
    ADD CONSTRAINT social_media_posts_approved_by_id_fkey FOREIGN KEY (approved_by_id) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: social_media_posts social_media_posts_article_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.social_media_posts
    ADD CONSTRAINT social_media_posts_article_id_fkey FOREIGN KEY (article_id) REFERENCES public.articles(id) ON DELETE CASCADE;


--
-- Name: social_media_posts social_media_posts_model_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.social_media_posts
    ADD CONSTRAINT social_media_posts_model_id_fkey FOREIGN KEY (model_id) REFERENCES public.ai_models(id) ON DELETE SET NULL;


--
-- Name: tags tags_approved_by_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tags
    ADD CONSTRAINT tags_approved_by_id_fkey FOREIGN KEY (approved_by_id) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: tour_attractions tour_attractions_attraction_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tour_attractions
    ADD CONSTRAINT tour_attractions_attraction_id_fkey FOREIGN KEY (attraction_id) REFERENCES public.attractions(id);


--
-- Name: tour_attractions tour_attractions_tour_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tour_attractions
    ADD CONSTRAINT tour_attractions_tour_id_fkey FOREIGN KEY (tour_id) REFERENCES public.tours(id);


--
-- Name: tours tours_zone_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tours
    ADD CONSTRAINT tours_zone_id_fkey FOREIGN KEY (zone_id) REFERENCES public.zones(id);


--
-- Name: translations translations_approved_by_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.translations
    ADD CONSTRAINT translations_approved_by_id_fkey FOREIGN KEY (approved_by_id) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: translations translations_generated_by_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.translations
    ADD CONSTRAINT translations_generated_by_id_fkey FOREIGN KEY (generated_by_id) REFERENCES public.ai_models(id) ON DELETE SET NULL;


--
-- Name: translations translations_language_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.translations
    ADD CONSTRAINT translations_language_fkey FOREIGN KEY (language) REFERENCES public.approved_languages(code) ON DELETE CASCADE;


--
-- Name: zone_attraction_rates zone_attraction_rates_attraction_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.zone_attraction_rates
    ADD CONSTRAINT zone_attraction_rates_attraction_id_fkey FOREIGN KEY (attraction_id) REFERENCES public.attractions(id);


--
-- Name: zone_attraction_rates zone_attraction_rates_zone_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.zone_attraction_rates
    ADD CONSTRAINT zone_attraction_rates_zone_id_fkey FOREIGN KEY (zone_id) REFERENCES public.zones(id);


--
-- Name: zone_transport_rates zone_transport_rates_from_zone_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.zone_transport_rates
    ADD CONSTRAINT zone_transport_rates_from_zone_id_fkey FOREIGN KEY (from_zone_id) REFERENCES public.zones(id);


--
-- Name: zone_transport_rates zone_transport_rates_to_zone_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.zone_transport_rates
    ADD CONSTRAINT zone_transport_rates_to_zone_id_fkey FOREIGN KEY (to_zone_id) REFERENCES public.zones(id);


--
-- PostgreSQL database dump complete
--

