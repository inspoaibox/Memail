package com.memail.mobile;

import java.util.LinkedHashSet;
import java.util.Locale;
import java.util.Set;

final class AccountRefs {
    private AccountRefs() {}

    static boolean sameId(String left, String right) {
        if (left == null || right == null) return false;
        String a = normalizeToken(left);
        String b = normalizeToken(right);
        return !a.isEmpty() && a.equalsIgnoreCase(b);
    }

    static boolean mailBelongsToAccount(Models.Mail mail, Models.Account account) {
        if (mail == null || account == null) return false;
        String type = normalizePlain(mail.accountType);
        String id = safe(mail.accountId);
        if (!type.isEmpty() && accountMatchesRef(account, type + ":" + id)) return true;
        return accountMatchesRef(account, id);
    }

    static boolean accountMatchesRef(Models.Account account, String ref) {
        if (account == null) return false;
        String normalized = normalizeRef(ref);
        return !normalized.isEmpty() && accountAliases(account).contains(normalized);
    }

    static boolean mailMatchesAccountRef(Models.Mail mail, String ref) {
        if (mail == null) return false;
        String normalized = normalizeRef(ref);
        if (normalized.isEmpty()) return false;
        Set<String> aliases = new LinkedHashSet<>();
        String type = normalizePlain(mail.accountType);
        String id = normalizeToken(mail.accountId);
        addAlias(aliases, id);
        if (!type.isEmpty()) addAlias(aliases, type + ":" + id);
        if ("local".equals(type)) addAlias(aliases, "localemail:" + id);
        return aliases.contains(normalized);
    }

    private static Set<String> accountAliases(Models.Account account) {
        Set<String> aliases = new LinkedHashSet<>();
        String type = normalizePlain(account.type);
        String id = normalizeToken(account.id);
        String email = normalizeToken(account.email);
        addAlias(aliases, id);
        addAlias(aliases, email);
        if ("local".equals(type)) {
            addAlias(aliases, "local:" + id);
            addAlias(aliases, "local:" + email);
            addAlias(aliases, "localemail:" + id);
            addAlias(aliases, "localemail:" + email);
        } else if ("external".equals(type)) {
            addAlias(aliases, "external:" + id);
            addAlias(aliases, "external:" + email);
            addAlias(aliases, "externalemail:" + email);
        }
        return aliases;
    }

    private static void addAlias(Set<String> aliases, String value) {
        String normalized = normalizeRef(value);
        if (!normalized.isEmpty()) aliases.add(normalized);
    }

    private static String normalizeRef(String value) {
        String raw = safe(value);
        if (raw.isEmpty()) return "";
        String lower = raw.toLowerCase(Locale.ROOT);
        if (lower.startsWith("localemail:")) return prefixedRef("localemail", raw.substring(11));
        if (lower.startsWith("externalemail:")) return prefixedRef("externalemail", raw.substring(14));
        if (lower.startsWith("local:")) return prefixedRef("local", raw.substring(6));
        if (lower.startsWith("external:")) return prefixedRef("external", raw.substring(9));
        return normalizePlain(raw);
    }

    private static String prefixedRef(String prefix, String value) {
        String token = normalizeToken(value);
        return token.isEmpty() ? "" : prefix + ":" + token;
    }

    private static String normalizeToken(String value) {
        String raw = safe(value);
        if (raw.isEmpty()) return "";
        String lower = raw.toLowerCase(Locale.ROOT);
        if (lower.startsWith("localemail:")) return normalizeToken(raw.substring(11));
        if (lower.startsWith("externalemail:")) return normalizeToken(raw.substring(14));
        if (lower.startsWith("local:")) return normalizeToken(raw.substring(6));
        if (lower.startsWith("external:")) return normalizeToken(raw.substring(9));
        return normalizePlain(raw);
    }

    private static String normalizePlain(String value) {
        return value == null ? "" : value.trim().toLowerCase(Locale.ROOT);
    }

    private static String safe(String value) {
        return value == null ? "" : value.trim();
    }
}
