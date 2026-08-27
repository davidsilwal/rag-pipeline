"use client";

import { create } from "zustand";

const TOKEN_KEY = "wiki_api_token";
const API_URL_KEY = "wiki_api_url";

const DEFAULT_API_URL =
  process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000/api/v1";

function readToken(): string {
  if (typeof window === "undefined") return "";
  return localStorage.getItem(TOKEN_KEY) || "";
}

function readApiUrl(): string {
  if (typeof window === "undefined") return DEFAULT_API_URL;
  return localStorage.getItem(API_URL_KEY) || DEFAULT_API_URL;
}

interface AuthState {
  token: string;
  apiUrl: string;
  isAuthenticated: boolean;
  setToken: (token: string) => void;
  setApiUrl: (url: string) => void;
  logout: () => void;
}

export const useAuth = create<AuthState>((set) => ({
  token: typeof window !== "undefined" ? (localStorage.getItem(TOKEN_KEY) || "") : "",
  apiUrl: typeof window !== "undefined" ? (localStorage.getItem(API_URL_KEY) || DEFAULT_API_URL) : DEFAULT_API_URL,
  isAuthenticated: typeof window !== "undefined" ? !!localStorage.getItem(TOKEN_KEY) : false,
  setToken: (token: string) => {
    if (typeof window !== "undefined") {
      localStorage.setItem(TOKEN_KEY, token);
    }
    set({ token, isAuthenticated: !!token });
  },
  setApiUrl: (url: string) => {
    if (typeof window !== "undefined") {
      localStorage.setItem(API_URL_KEY, url);
    }
    set({ apiUrl: url });
  },
  logout: () => {
    if (typeof window !== "undefined") {
      localStorage.removeItem(TOKEN_KEY);
    }
    set({ token: "", isAuthenticated: false });
  },
}));

// Sync localStorage on client load (handles tab changes etc.)
if (typeof window !== "undefined") {
  const token = readToken();
  const apiUrl = readApiUrl();
  useAuth.setState({ token, apiUrl, isAuthenticated: !!token });
}
