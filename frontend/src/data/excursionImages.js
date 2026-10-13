// Image configuration for academic excursion landing pages
// Each excursion has an array of images - first image is the default/main image
// All images are 790x640 aspect ratio

export const excursionImages = {
    'casco-antiguo-plazas': [
        { id: 1, src: '/images/plazas-and-churches.png', alt: 'Plazas y Iglesias del Casco Antiguo' },
        { id: 2, src: '/images/iglesia-de-san-jose.jpg', alt: 'Iglesia de San José' },
        { id: 3, src: '/images/arco-chato-tour.jpg', alt: 'Arco Chato' },
        { id: 4, src: '/images/catedral-interior.jpg', alt: 'Interior de la Catedral' },
        { id: 5, src: '/images/cupula-san-francisco.jpg', alt: 'Cúpula de San Francisco' },
        { id: 6, src: '/images/mosaiquillo-virgen.jpg', alt: 'Mosaico de la Virgen' },
        { id: 7, src: '/images/plaza-herrera.jpg', alt: 'Plaza Herrera' },
        { id: 8, src: '/images/sagrario.jpg', alt: 'El Sagrario' },
    ],
    'casco-antiguo-museos': [
        { id: 1, src: '/images/museum-circuit.png', alt: 'Circuito de Museos' },
        { id: 2, src: '/images/campana-la-merced.jpg', alt: 'Campana de La Merced' },
        { id: 3, src: '/images/museo-del-canal-exterior.jpg', alt: 'Museo del Canal - Exterior' },
        { id: 4, src: '/images/museo-del-canal-interior.jpg', alt: 'Museo del Canal - Interior' },
        { id: 5, src: '/images/museo-del-canal-lampara-faro.jpg', alt: 'Lámpara del Faro' },
        { id: 6, src: '/images/dos-molas-rojas.jpg', alt: 'Molas Tradicionales' },
    ],
    'panama-viejo': [
        { id: 1, src: '/images/panama-viejo.png', alt: 'Ruinas de Panamá Viejo' },
        { id: 2, src: '/images/panama-viejo-maqueta.jpg', alt: 'Maqueta de Panamá Viejo' },
        { id: 3, src: '/images/panama-viejo-ruina.jpg', alt: 'Torre de la Catedral' },
        { id: 4, src: '/images/panama-viejo-skyline.jpg', alt: 'Vista al Skyline Moderno' },
    ],
    'biomuseo': [
        { id: 1, src: '/images/biomuseo.jpg', alt: 'Biomuseo de Panamá' },
        { id: 2, src: '/images/biomuseo-acuario.jpg', alt: 'Acuario del Biomuseo' },
        { id: 3, src: '/images/biomuseo-diversidad.jpg', alt: 'Galería de Biodiversidad' },
        { id: 4, src: '/images/biomuseo-megalodont.jpg', alt: 'Fósil de Megalodonte' },
    ],
    'miraflores': [
        { id: 1, src: '/images/miraflores.jpg', alt: 'Esclusas de Miraflores' },
        { id: 2, src: '/images/barco-en-miraflores.jpg', alt: 'Barco en las Esclusas' },
        { id: 3, src: '/images/remolcador.jpg', alt: 'Remolcador del Canal' },
    ],
    'inter-religiosa': [
        { id: 1, src: '/images/inter-religious.png', alt: 'Diversidad Religiosa' },
        { id: 2, src: '/images/sinagoga-interior.jpg', alt: 'Interior de la Sinagoga' },
        { id: 3, src: '/images/bahai.jpg', alt: 'Templo Bahá\'í' },
    ],
}

// Helper to get images by excursion key
export const getExcursionImages = (key) => excursionImages[key] || []
