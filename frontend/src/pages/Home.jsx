import HeroSection from '../components/sections/home/HeroSection.jsx'
import SchoolsSection from '../components/sections/home/SchoolsSection.jsx'
import MailingListEducators from '../components/sections/home/MailingListEducators.jsx'
import TouristsSection from '../components/sections/home/TouristsSection.jsx'
import MeetGuideSection from '../components/sections/home/MeetGuideSection.jsx'
import FeaturedArticles from '../components/sections/home/FeaturedArticles.jsx'
import MailingListBlog from '../components/sections/home/MailingListBlog.jsx'
import CTASection from '../components/sections/home/CTASection.jsx'

export default function Home() {
    return (
        <>
            <HeroSection />
            <SchoolsSection />
            <MailingListEducators />
            <TouristsSection />
            <MeetGuideSection />
            <FeaturedArticles />
            <MailingListBlog />
            <CTASection />
        </>
    )
}
