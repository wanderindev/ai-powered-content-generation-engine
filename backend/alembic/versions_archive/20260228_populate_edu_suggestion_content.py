"""Populate description and sub_topics for excursion-based edu suggestions.

Revision ID: 20260228_populate_edu_suggestion_content
Revises: 20260228_seed_edu_suggestions
Create Date: 2026-02-28
"""

from alembic import op
from sqlalchemy import text

revision = "20260228_populate_edu_sug"
down_revision = "20260228_seed_edu_suggestions"


CONTENT = [
    {
        "key": "casco-antiguo-plazas",
        "description": (
            "Explore UNESCO-recognized Casco Antiguo through its magnificent plazas "
            "and historic churches. Students trace Panama's evolution from Spanish "
            "colony to independent republic through colonial architecture, examining "
            "the independence movement of 1903, Bolivar's political vision, and the "
            "role of religious institutions in colonial society."
        ),
        "sub_topics": [
            "Colonial Architecture: Plaza design, church construction, and urban planning in Spanish colonial Casco Antiguo",
            "Panamanian Independence: The geopolitical forces and key events leading to Panama's 1903 separation from Colombia",
            "Bolivar and the Amphictyonic Congress: Simon Bolivar's vision for Latin American unity and its connection to Panama",
            "Religious Heritage: The role of Catholic churches and religious orders in Panama's colonial society",
            "UNESCO World Heritage: Conservation challenges and the significance of Casco Antiguo's designation",
        ],
    },
    {
        "key": "casco-antiguo-museos",
        "description": (
            "Trace Panama's evolution from Spanish colony to modern republic through "
            "a curated museum circuit in Casco Antiguo. Students engage with the "
            "Interoceanic Canal Museum, History Museum, Mola Museum, and Bolivar "
            "Palace to understand how Panama's strategic position shaped its "
            "cultural, political, and economic development."
        ),
        "sub_topics": [
            "Interoceanic Canal History: The engineering and geopolitical story of the canal that transformed global shipping",
            "Colonial to Republican Transition: How Panama evolved from a Spanish colony through Colombian province to independent republic",
            "Indigenous Cultural Heritage: Kuna mola artistry and the preservation of indigenous cultural traditions in modern Panama",
            "Diplomatic History: The role of Bolivar Palace and Panama's position in hemispheric diplomacy",
            "Museum Pedagogy: How Casco Antiguo's museums preserve and present Panama's multilayered history",
        ],
    },
    {
        "key": "panama-viejo",
        "description": (
            "Step onto the excavated streets of the first European settlement on "
            "the Pacific coast, founded in 1519 and destroyed by pirate Henry "
            "Morgan in 1671. Students explore archaeological ruins, climb the "
            "restored Cathedral Tower, and learn how archaeologists piece together "
            "historical narratives from physical evidence."
        ),
        "sub_topics": [
            "Colonial Founding and Urban Layout: The establishment and design of Panama Viejo as a strategic colonial hub in 1519",
            "Morgan's Raid of 1671: The pirate attack that destroyed the city and forced relocation to Casco Antiguo",
            "Archaeological Methods: How researchers use physical evidence to reconstruct daily life in colonial Panama",
            "Pizarro and the Conquest: Panama Viejo's role as a staging area for Francisco Pizarro's conquest of the Inca Empire",
            "Colonial Trade Networks: How the city functioned as a key node in Spain's Pacific trade and treasure routes",
        ],
    },
    {
        "key": "biomuseo",
        "description": (
            "Inside Frank Gehry's only Latin American architectural masterpiece, "
            "discover how Panama literally changed Earth's biology by uniting two "
            "continents and dividing a vast ocean. Students investigate the Great "
            "American Biotic Interchange and explore how this narrow isthmus "
            "triggered one of the most significant biodiversity events in Earth's history."
        ),
        "sub_topics": [
            "Isthmus Formation: The volcanic and tectonic processes that created the land bridge connecting North and South America",
            "Great American Biotic Interchange: How species migrated between continents after the isthmus formed, reshaping ecosystems",
            "Ocean Current Disruption: How the isthmus divided the Atlantic and Pacific, altering global ocean circulation and climate",
            "Panama's Biodiversity: The extraordinary species richness of Panama's tropical ecosystems and their ecological significance",
            "Human-Environment Interaction: Fifteen thousand years of human impact on Panama's environment from indigenous peoples to modern development",
        ],
    },
    {
        "key": "miraflores",
        "description": (
            "Witness the engineering marvel of the Panama Canal as massive ships "
            "navigate the Miraflores Locks. Students observe the precision "
            "operations of gates, water systems, and electric mules while learning "
            "how abstract concepts of engineering and global trade become tangible, "
            "observable phenomena."
        ),
        "sub_topics": [
            "Lock Engineering: How the Miraflores Locks raise and lower ships using gravity-fed water chambers",
            "Canal Construction History: The French and American canal-building efforts and the human cost of construction",
            "Global Trade Impact: How the Panama Canal transformed international shipping routes and global economics",
            "Science in Action: Observable physical science principles including water displacement, gravity, and mechanical engineering",
        ],
    },
    {
        "key": "inter-religiosa-abrahamica",
        "description": (
            "Explore Panama's remarkable Abrahamic religious diversity by visiting "
            "the Greek Orthodox Cathedral, Kol Shearith Synagogue, Jama Masjid "
            "Mosque, and Continental Bahai Temple. Students learn about the history, "
            "beliefs, and cultural contributions of these faith communities through "
            "direct interaction with religious leaders."
        ),
        "sub_topics": [
            "Christianity in Panama: The Orthodox tradition and how Greek immigrant communities established their faith in Panama",
            "Jewish Heritage: The history of Panama's Jewish community and the role of Kol Shearith Synagogue",
            "Islam in Panama: The establishment of the Muslim community and Jama Masjid Mosque in the context of Panamanian society",
            "Bahai Faith: The Continental Bahai Temple and the growth of the Bahai community in Latin America",
            "Religious Coexistence: How Panama's strategic position as a crossroads fostered interfaith tolerance and diversity",
        ],
    },
    {
        "key": "inter-religiosa-oriental",
        "description": (
            "Discover Panama's Eastern religious traditions by visiting the Hindu "
            "Temple of Panama and the Soka Gakkai International Cultural Center. "
            "Students examine how immigrant communities from South and East Asia "
            "brought their spiritual traditions to Panama and adapted them within "
            "a predominantly Catholic society."
        ),
        "sub_topics": [
            "Hinduism in Panama: The Indian immigrant community and the establishment of the Hindu Temple of Panama",
            "Buddhism in Panama: Soka Gakkai International and the spread of Nichiren Buddhist practice in Latin America",
            "Asian Immigration History: How South and East Asian immigrants arrived in Panama through canal construction and trade",
            "Cultural Adaptation: How Eastern religious communities adapted their practices within Panamanian society",
            "Comparative Religion: Analyzing similarities and differences between Eastern and Western religious traditions present in Panama",
        ],
    },
]


def upgrade() -> None:
    conn = op.get_bind()
    for item in CONTENT:
        topics = "{" + ",".join(f'"{t}"' for t in item["sub_topics"]) + "}"
        conn.execute(
            text(
                "UPDATE edu_suggestions "
                "SET description = :description, sub_topics = :sub_topics "
                "WHERE excursion_key = :key"
            ),
            {
                "key": item["key"],
                "description": item["description"],
                "sub_topics": topics,
            },
        )


def downgrade() -> None:
    conn = op.get_bind()
    keys = [item["key"] for item in CONTENT]
    conn.execute(
        text(
            "UPDATE edu_suggestions "
            "SET description = NULL, sub_topics = NULL "
            "WHERE excursion_key = ANY(:keys)"
        ),
        {"keys": keys},
    )
