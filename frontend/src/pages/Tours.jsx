import ToursHero from '../components/sections/tours/ToursHero.jsx'
import AudienceTypes from '../components/sections/tours/AudienceTypes.jsx'
import Highlights from '../components/sections/tours/Highlights.jsx'
import DestinationGrid from '../components/sections/tours/DestinationGrid.jsx'
import ToursCTA from '../components/sections/tours/ToursCTA.jsx'

export default function Tours() {
    return (
        <>
            <ToursHero />
            <AudienceTypes />
            <Highlights />
            <DestinationGrid />
            <ToursCTA />
        </>
    )
}
