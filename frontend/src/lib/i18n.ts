import i18next from "i18next";
import { initReactI18next } from "react-i18next";

import en from "@/locales/en.json";
import fr from "@/locales/fr.json";

const STORAGE_KEY = "sb.lang";

export function initI18n() {
  const stored =
    (typeof window !== "undefined" && window.localStorage.getItem(STORAGE_KEY)) ||
    (typeof navigator !== "undefined" && navigator.language.startsWith("fr") ? "fr" : "en");

  void i18next.use(initReactI18next).init({
    resources: {
      en: { translation: en },
      fr: { translation: fr },
    },
    lng: stored,
    fallbackLng: "en",
    interpolation: { escapeValue: false },
  });
}

export function setLanguage(lng: "en" | "fr") {
  void i18next.changeLanguage(lng);
  if (typeof window !== "undefined") window.localStorage.setItem(STORAGE_KEY, lng);
}

export function currentLanguage(): "en" | "fr" {
  return (i18next.language as "en" | "fr") || "en";
}
