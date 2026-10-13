from app.models.article import Article
from app.models.article_suggestion import ArticleSuggestion
from app.models.attraction import Attraction
from app.models.booking import Booking, BookingStatus, BookingStatusLog
from app.models.category import Category, Taxonomy
from app.models.contact_submission import ContactSubmission
from app.models.crawl_job import CrawlJob, CrawlJobStatus, CrawlJobType
from app.models.edu_material import EduMaterial
from app.models.edu_research import EduResearch
from app.models.edu_slide import EduSlide
from app.models.edu_suggestion import EduSuggestion
from app.models.educator import Educator
from app.models.enums import (
    ContactInterest,
    ContentStatus,
    EducatorStatus,
    Language,
    MaterialType,
    MediaSource,
    MediaType,
    TimeSlot,
    TransportDirection,
)
from app.models.fact_check import (
    FactCheckClaim,
    FactCheckMethod,
    FactCheckSubject,
    FactCheckVerdict,
    FactClaimType,
)
from app.models.hotel import Hotel
from app.models.instagram_post import InstagramPost
from app.models.magic_link import AdminEmail, MagicLink, MagicLinkAction
from app.models.media import Media, media_tags
from app.models.media_candidate import CandidateSource, CandidateStatus, MediaCandidate
from app.models.order import Order, OrderStatus, PaymentMethod
from app.models.pricing_config import PricingConfig
from app.models.research import Research
from app.models.tag import Tag, article_tags
from app.models.tour import Tour, TourAttraction
from app.models.user import User
from app.models.verified_claim import VerifiedClaim
from app.models.zone import Zone
from app.models.zone_attraction_rate import ZoneAttractionRate
from app.models.zone_transport_rate import ZoneTransportRate

__all__ = [
    # Existing models
    "Article",
    "ArticleSuggestion",
    "Category",
    "ContactSubmission",
    "Taxonomy",
    "Media",
    "MediaCandidate",
    "MediaSource",
    "MediaType",
    "CandidateSource",
    "CandidateStatus",
    "ContentStatus",
    "CrawlJob",
    "CrawlJobStatus",
    "CrawlJobType",
    "Tag",
    "article_tags",
    "media_tags",
    # Research pipeline
    "Research",
    "User",
    # Tour booking models
    "Attraction",
    "Booking",
    "BookingStatus",
    "BookingStatusLog",
    "TimeSlot",
    "TransportDirection",
    "Hotel",
    "AdminEmail",
    "MagicLink",
    "MagicLinkAction",
    "Order",
    "OrderStatus",
    "PaymentMethod",
    "Language",
    "PricingConfig",
    "Tour",
    "TourAttraction",
    "Zone",
    "ZoneAttractionRate",
    "ZoneTransportRate",
    # Educational content pipeline
    "EduSuggestion",
    "EduResearch",
    "EduMaterial",
    "EduSlide",
    "MaterialType",
    "Educator",
    "EducatorStatus",
    # Contact form
    "ContactInterest",
    # Fact-check pipeline (epic #373)
    "FactCheckClaim",
    "FactCheckMethod",
    "FactCheckSubject",
    "FactCheckVerdict",
    "FactClaimType",
    "VerifiedClaim",
    # Instagram / social pipeline
    "InstagramPost",
]
