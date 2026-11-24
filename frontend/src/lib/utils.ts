import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

/** shadcn/ui's class merger: conditional classes via clsx, Tailwind conflicts resolved by twMerge. */
export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}
