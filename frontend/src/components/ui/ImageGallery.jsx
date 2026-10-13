import { useState } from 'react'

export default function ImageGallery({ images = [], className = '' }) {
    const [selectedIndex, setSelectedIndex] = useState(0)

    // If no images provided, show nothing
    if (!images.length) return null

    const selectedImage = images[selectedIndex]
    const thumbnails = images.length > 1 ? images : []

    // Calculate if we need 2 rows (more than 4 thumbnails)
    const hasTwoRows = thumbnails.length > 4

    return (
        <div className={`flex flex-col ${className}`}>
            {/* Main Image - use viewport height to ensure thumbnails fit */}
            <div className="w-full">
                <img
                    src={selectedImage.src}
                    alt={selectedImage.alt || ''}
                    className={`w-full object-cover rounded-xl bg-gray-100 shadow-xl ring-1 ring-gray-400/10
                        ${hasTwoRows
                            ? 'lg:h-[calc(100vh-16rem)] lg:max-h-[500px]'
                            : 'lg:h-[calc(100vh-12rem)] lg:max-h-[600px]'
                        } aspect-[790/640] lg:aspect-auto`}
                />
            </div>

            {/* Thumbnails - only show if more than 1 image */}
            {thumbnails.length > 1 && (
                <div className="mt-3 grid grid-cols-4 gap-2">
                    {thumbnails.map((image, index) => (
                        <button
                            key={image.id || index}
                            onClick={() => setSelectedIndex(index)}
                            className={`relative aspect-[790/640] overflow-hidden rounded-lg bg-gray-100 transition-all
                                ${selectedIndex === index
                                    ? 'ring-2 ring-indigo-600 ring-offset-2'
                                    : 'hover:opacity-80 ring-1 ring-gray-200'
                                }`}
                        >
                            <img
                                src={image.src}
                                alt={image.alt || ''}
                                className="w-full h-full object-cover"
                            />
                        </button>
                    ))}
                </div>
            )}
        </div>
    )
}
