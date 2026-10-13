import { createContext, useContext } from 'react';

export const EducatorAuthContext = createContext(null);

export function useEducatorAuth() {
  const context = useContext(EducatorAuthContext);
  if (!context) {
    throw new Error('useEducatorAuth must be used within EducatorAuthProvider');
  }
  return context;
}
