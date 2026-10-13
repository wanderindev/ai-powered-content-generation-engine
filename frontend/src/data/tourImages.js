// Image configuration for tour landing pages
// Tours share images with their equivalent excursions (except Mi Pueblito)
// All images are 790x640 aspect ratio

export const tourImages = {
    'canal': [
        { id: 1, src: '/images/miraflores.jpg', alt: 'Miraflores Locks' },
        { id: 2, src: '/images/barco-en-miraflores.jpg', alt: 'Ship in the Locks' },
        { id: 3, src: '/images/remolcador.jpg', alt: 'Canal Tugboat' },
    ],
    'casco-plazas': [
        { id: 1, src: '/images/plazas-and-churches.png', alt: 'Casco Antiguo Plazas and Churches' },
        { id: 2, src: '/images/iglesia-de-san-jose.jpg', alt: 'San José Church' },
        { id: 3, src: '/images/arco-chato-tour.jpg', alt: 'Arco Chato' },
        { id: 4, src: '/images/catedral-interior.jpg', alt: 'Cathedral Interior' },
        { id: 5, src: '/images/cupula-san-francisco.jpg', alt: 'San Francisco Dome' },
        { id: 6, src: '/images/mosaiquillo-virgen.jpg', alt: 'Virgin Mosaic' },
        { id: 7, src: '/images/plaza-herrera.jpg', alt: 'Plaza Herrera' },
        { id: 8, src: '/images/sagrario.jpg', alt: 'The Tabernacle' },
    ],
    'casco-museums': [
        { id: 1, src: '/images/museum-circuit.png', alt: 'Museum Circuit' },
        { id: 2, src: '/images/campana-la-merced.jpg', alt: 'La Merced Bell' },
        { id: 3, src: '/images/museo-del-canal-exterior.jpg', alt: 'Canal Museum - Exterior' },
        { id: 4, src: '/images/museo-del-canal-interior.jpg', alt: 'Canal Museum - Interior' },
        { id: 5, src: '/images/museo-del-canal-lampara-faro.jpg', alt: 'Lighthouse Lamp' },
        { id: 6, src: '/images/dos-molas-rojas.jpg', alt: 'Traditional Molas' },
    ],
    'panama-viejo': [
        { id: 1, src: '/images/panama-viejo.png', alt: 'Panama Viejo Ruins' },
        { id: 2, src: '/images/panama-viejo-maqueta.jpg', alt: 'Panama Viejo Model' },
        { id: 3, src: '/images/panama-viejo-ruina.jpg', alt: 'Cathedral Tower' },
        { id: 4, src: '/images/panama-viejo-skyline.jpg', alt: 'Modern Skyline View' },
    ],
    'biomuseo': [
        { id: 1, src: '/images/biomuseo.jpg', alt: 'Biomuseo Panama' },
        { id: 2, src: '/images/biomuseo-acuario.jpg', alt: 'Biomuseo Aquarium' },
        { id: 3, src: '/images/biomuseo-diversidad.jpg', alt: 'Biodiversity Gallery' },
        { id: 4, src: '/images/biomuseo-megalodont.jpg', alt: 'Megalodon Fossil' },
    ],
    'siete-iglesias': [
        { id: 1, src: '/images/san-francisco-frente.jpg', alt: 'Church of San Francisco de Asís' },
        { id: 2, src: '/images/plazas-and-churches.png', alt: 'Churches of Casco Antiguo' },
        { id: 3, src: '/images/iglesia-de-san-jose.jpg', alt: 'San José Church' },
        { id: 4, src: '/images/arco-chato-tour.jpg', alt: 'Arco Chato' },
        { id: 5, src: '/images/catedral-interior.jpg', alt: 'Cathedral Interior' },
        { id: 6, src: '/images/cupula-san-francisco.jpg', alt: 'San Francisco Dome' },
        { id: 7, src: '/images/mosaiquillo-virgen.jpg', alt: 'Virgin Mosaic' },
        { id: 8, src: '/images/sagrario.jpg', alt: 'The Tabernacle' },
    ],
    'mi-pueblito': [
        { id: 1, src: '/images/mi-pueblito-capilla.jpg', alt: 'Colonial Chapel' },
        { id: 2, src: '/images/mi-pueblito-capilla-interior.jpg', alt: 'Chapel Interior' },
        { id: 3, src: '/images/mi-pueblito-pueblo-antillano.jpg', alt: 'West Indian Village' },
        { id: 4, src: '/images/mi-pueblito-casa-antillana.jpg', alt: 'West Indian House' },
        { id: 5, src: '/images/mi-pueblito-mural.jpg', alt: 'Cultural Heritage Mural' },
        { id: 6, src: '/images/mi-pueblito-horno.jpg', alt: 'Traditional Clay Oven' },
        { id: 7, src: '/images/mi-pueblito-bienvenidos.jpg', alt: 'Mi Pueblito Welcome Sign' },
    ],
}

// Helper to get images by tour key
export const getTourImages = (key) => tourImages[key] || []
