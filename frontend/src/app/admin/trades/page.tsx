import { redirect } from "next/navigation";

/** Trades UI lives at `/trades`; this path is a stable alias for bookmarks and admin links. */
export default function AdminTradesAliasPage() {
  redirect("/trades");
}
