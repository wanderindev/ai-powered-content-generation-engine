import { useState } from 'react'
import { Dialog, DialogPanel } from '@headlessui/react'
import { Bars3Icon, XMarkIcon } from '@heroicons/react/24/outline'
import { Link, useLocation } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import CartIcon from '../booking/CartIcon'

export default function Navbar() {
    const [mobileMenuOpen, setMobileMenuOpen] = useState(false)
    const location = useLocation()
    const { t, i18n } = useTranslation()

    const langPrefix = i18n.language === 'es' ? '/es' : '/en'

    const navigation = [
        { name: t('nav.home'), href: '/' },
        { name: t('nav.excursions'), href: '/excursiones-academicas' },
        { name: t('nav.tours'), href: '/tours-guiados' },
        { name: t('nav.blog'), href: `${langPrefix}/blog` },
        { name: t('nav.edu_materials'), href: '/materiales-educativos' },
        { name: t('nav.contact'), href: '/contacto' },
    ]

    const toggleLanguage = () => {
        i18n.changeLanguage(i18n.language === 'en' ? 'es' : 'en')
    }

    return (
        <header className="bg-white h-[120px] flex items-center">
            <nav aria-label="Global" className="mx-auto flex w-full max-w-7xl items-center justify-between p-6 lg:px-8">
                <div className="flex lg:flex-1">
                    <Link to="/" className="-m-1.5 p-1.5">
                        <span className="sr-only">Panama In Context</span>
                        <img
                            alt="Panama In Context"
                            src="/logo-h-116.png"
                            className="h-[90px] w-auto"
                        />
                    </Link>
                </div>
                <div className="flex lg:hidden items-center gap-x-4">
                    <CartIcon />
                    <button
                        type="button"
                        onClick={() => setMobileMenuOpen(true)}
                        className="-m-2.5 inline-flex items-center justify-center rounded-md p-2.5 text-gray-700"
                    >
                        <span className="sr-only">{t('nav.open_menu')}</span>
                        <Bars3Icon aria-hidden="true" className="size-6" />
                    </button>
                </div>
                <div className="hidden lg:flex lg:gap-x-12">
                    {navigation.map((item) => (
                        <Link
                            key={item.href} // changed to href as name changes with dynamic translation
                            to={item.href}
                            className={`text-sm/6 font-semibold ${location.pathname === item.href ? 'text-indigo-600' : 'text-gray-900'
                                }`}
                        >
                            {item.name}
                        </Link>
                    ))}
                </div>
                <div className="hidden lg:flex lg:flex-1 lg:justify-end items-center gap-x-6">
                    <CartIcon />
                    <div className="flex items-center rounded-full bg-gray-100 p-1">
                        <button
                            onClick={() => i18n.changeLanguage('en')}
                            className={`rounded-full px-3 py-1 text-sm font-semibold transition-all ${i18n.language === 'en'
                                ? 'bg-white text-indigo-600 shadow-sm'
                                : 'text-gray-500 hover:text-gray-900'
                                }`}
                        >
                            EN
                        </button>
                        <button
                            onClick={() => i18n.changeLanguage('es')}
                            className={`rounded-full px-3 py-1 text-sm font-semibold transition-all ${i18n.language === 'es'
                                ? 'bg-white text-indigo-600 shadow-sm'
                                : 'text-gray-500 hover:text-gray-900'
                                }`}
                        >
                            ES
                        </button>
                    </div>
                </div>
            </nav>
            <Dialog open={mobileMenuOpen} onClose={setMobileMenuOpen} className="lg:hidden">
                <div className="fixed inset-0 z-50" />
                <DialogPanel className="fixed inset-y-0 right-0 z-50 w-full overflow-y-auto bg-white p-6 sm:max-w-sm sm:ring-1 sm:ring-gray-900/10">
                    <div className="flex items-center justify-between">
                        <Link to="/" className="-m-1.5 p-1.5" onClick={() => setMobileMenuOpen(false)}>
                            <span className="sr-only">Panama In Context</span>
                            <img
                                alt=""
                                src="/logo-h-116.png"
                                className="h-[90px] w-auto"
                            />
                        </Link>
                        <button
                            type="button"
                            onClick={() => setMobileMenuOpen(false)}
                            className="-m-2.5 rounded-md p-2.5 text-gray-700"
                        >
                            <span className="sr-only">{t('nav.close_menu')}</span>
                            <XMarkIcon aria-hidden="true" className="size-6" />
                        </button>
                    </div>
                    <div className="mt-6 flow-root">
                        <div className="-my-6 divide-y divide-gray-500/10">
                            <div className="space-y-2 py-6">
                                {navigation.map((item) => (
                                    <Link
                                        key={item.href}
                                        to={item.href}
                                        onClick={() => setMobileMenuOpen(false)}
                                        className="-mx-3 block rounded-lg px-3 py-2 text-base/7 font-semibold text-gray-900 hover:bg-gray-50"
                                    >
                                        {item.name}
                                    </Link>
                                ))}
                            </div>
                            <div className="py-6">
                                <button
                                    onClick={() => {
                                        toggleLanguage();
                                        setMobileMenuOpen(false);
                                    }}
                                    className="-mx-3 block rounded-lg px-3 py-2.5 text-base font-semibold leading-7 text-gray-900 hover:bg-gray-50"
                                >
                                    {i18n.language === 'en' ? 'Cambiar a Español' : 'Switch to English'}
                                </button>
                            </div>
                        </div>
                    </div>
                </DialogPanel>
            </Dialog>
        </header>
    )
}
