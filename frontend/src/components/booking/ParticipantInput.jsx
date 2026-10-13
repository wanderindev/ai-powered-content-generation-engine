import React from 'react';
import { useTranslation } from 'react-i18next';
import { PlusIcon, MinusIcon, InformationCircleIcon } from '@heroicons/react/20/solid';

export default function ParticipantInput({ participants, onChange, maxParticipants = 15 }) {
    const { t } = useTranslation();

    // Categories define the rows
    const categories = [
        { key: 'seniorFemale', label: t('booking.modal.seniors_female') },
        { key: 'seniorMale', label: t('booking.modal.seniors_male') },
        { key: 'adult', label: t('booking.modal.adults') },
        { key: 'child10to17', label: t('booking.modal.children_10_17') },
        { key: 'child5to10', label: t('booking.modal.children_5_10') },
        { key: 'childUnder5', label: t('booking.modal.children_under_5') },
    ];

    // Helper to calculate total count
    const totalCount = Object.values(participants).reduce(
        (sum, cat) => sum + (cat.resident || 0) + (cat.tourist || 0),
        0
    );

    const handleUpdate = (categoryKey, type, delta) => {
        const currentCategory = participants[categoryKey] || { resident: 0, tourist: 0 };
        const currentValue = currentCategory[type];
        const newValue = currentValue + delta;

        // Prevent negative values
        if (newValue < 0) return;

        // Prevent exceeding max total
        if (delta > 0 && totalCount >= maxParticipants) return;

        // Update state
        onChange({
            ...participants,
            [categoryKey]: {
                ...currentCategory,
                [type]: newValue,
            },
        });
    };

    return (
        <div className="space-y-4">
            <div className="flex items-center justify-between">
                <h3 className="text-sm font-medium text-gray-900">{t('booking.modal.your_group')}</h3>
                <span className={`text-xs ${totalCount >= maxParticipants ? 'text-red-600 font-medium' : 'text-gray-500'}`}>
                    {t('booking.modal.total_participants', { count: totalCount })}
                </span>
            </div>

            <div className="bg-gray-50 p-3 rounded-md text-xs text-gray-500 flex items-start gap-2">
                <InformationCircleIcon className="h-4 w-4 flex-shrink-0 mt-0.5 text-gray-400" />
                <div>
                    <p>{t('booking.modal.group_note')}</p>
                    <p className="mt-1 text-gray-400">{t('booking.modal.resident_info')}</p>
                </div>
            </div>

            {totalCount >= maxParticipants && (
                <div className="bg-amber-50 border border-amber-200 p-3 rounded-md text-sm text-amber-800">
                    {t('booking.modal.group_too_large')}
                </div>
            )}

            {/* Grid Header */}
            <div className="grid grid-cols-12 gap-2 text-xs font-medium text-gray-500 border-b border-gray-200 pb-2">
                <div className="col-span-4"></div>
                <div className="col-span-4 text-center">{t('booking.modal.column_residents')}</div>
                <div className="col-span-4 text-center">{t('booking.modal.column_tourists')}</div>
            </div>

            {/* Grid Rows */}
            <div className="space-y-4">
                {categories.map((cat) => {
                    const catData = participants[cat.key] || { resident: 0, tourist: 0 };
                    return (
                    <div key={cat.key} className="grid grid-cols-12 gap-2 items-center">
                        {/* Label */}
                        <div className="col-span-4 text-sm text-gray-700">
                            {cat.label}
                        </div>

                        {/* Resident Controls */}
                        <div className="col-span-4 flex items-center justify-center space-x-2">
                            <button
                                type="button"
                                onClick={() => handleUpdate(cat.key, 'resident', -1)}
                                disabled={catData.resident <= 0}
                                className="p-1 rounded-full text-gray-400 hover:text-gray-500 disabled:opacity-30 disabled:cursor-not-allowed hover:bg-gray-100"
                            >
                                <MinusIcon className="h-4 w-4" />
                            </button>
                            <span className="w-4 text-center text-sm font-medium text-gray-900">
                                {catData.resident}
                            </span>
                            <button
                                type="button"
                                onClick={() => handleUpdate(cat.key, 'resident', 1)}
                                disabled={totalCount >= maxParticipants}
                                className="p-1 rounded-full text-indigo-600 hover:text-indigo-700 disabled:opacity-30 disabled:cursor-not-allowed hover:bg-indigo-50"
                            >
                                <PlusIcon className="h-4 w-4" />
                            </button>
                        </div>

                        {/* Tourist Controls */}
                        <div className="col-span-4 flex items-center justify-center space-x-2">
                            <button
                                type="button"
                                onClick={() => handleUpdate(cat.key, 'tourist', -1)}
                                disabled={catData.tourist <= 0}
                                className="p-1 rounded-full text-gray-400 hover:text-gray-500 disabled:opacity-30 disabled:cursor-not-allowed hover:bg-gray-100"
                            >
                                <MinusIcon className="h-4 w-4" />
                            </button>
                            <span className="w-4 text-center text-sm font-medium text-gray-900">
                                {catData.tourist}
                            </span>
                            <button
                                type="button"
                                onClick={() => handleUpdate(cat.key, 'tourist', 1)}
                                disabled={totalCount >= maxParticipants}
                                className="p-1 rounded-full text-indigo-600 hover:text-indigo-700 disabled:opacity-30 disabled:cursor-not-allowed hover:bg-indigo-50"
                            >
                                <PlusIcon className="h-4 w-4" />
                            </button>
                        </div>
                    </div>
                    );
                })}
            </div>
        </div>
    );
}
