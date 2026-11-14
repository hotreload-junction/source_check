"""Documented influence networks: hosts published by government / research teams (data only, no logic).

PORTAL_KOMBAT: the Russian "Portal Kombat" / "Pravda" network, from VIGINUM (SGDSN, France), TLP:CLEAR,
file 20241227_SGDSN_VIGINUM_NP_TLP-CLEAR_Portal-Kombat-domains.csv in
https://github.com/VIGINUM-FR/Rapports-Techniques (folder "202402 - Portal Kombat"), list dated 27 Dec 2024.
371 rows -> 233 hosts (subdomains whose listed parent already covers them are dropped). DFRLab/CheckFirst (Feb 2025)
document the same network consolidating onto news-pravda.com subdomains, which this list covers.

Match by HOST SUFFIX on the listed entry (signals/networks.py), never by registrable domain: the list contains
hosts such as lenta.kharkiv.ua, and kharkiv.ua as a whole is an ordinary Ukrainian city zone.
Regenerate from the CSV; do not hand-edit entries.
"""
from __future__ import annotations

PORTAL_KOMBAT_SOURCE = ("VIGINUM (SGDSN), Portal Kombat domain list, 27 Dec 2024, "
                        "github.com/VIGINUM-FR/Rapports-Techniques")

PORTAL_KOMBAT_HOSTS: frozenset[str] = frozenset({
    "abakan-news.net", "adygheya-news.net", "alchevsk-news.ru", "altay-news.net",
    "amur-news.net", "arkhangelsk-news.net", "astrakhan-news.net", "baikal-news.net",
    "barnaul-news.net", "bc-news.ru", "belgorod-news.net", "berdyansk-news.ru",
    "birobidzhan-news.net", "bryansk-news.net", "cheb-news.net", "chelyabinsk-news.net",
    "cherkassy-news.ru", "cherkessk-news.net", "chernigov-news.ru", "chernovcy-news.ru",
    "chita-news.net", "chukotka-news.net", "crimea-news.com", "dagestan-news.net",
    "dnepr-news.ru", "dneprnews.com.ua", "dneprodzerzhinsk-news.ru", "dnr-news.ru",
    "donetsk-news.ru", "feed-news.com", "gazeta.kharkiv.ua", "gorlovka-news.ru",
    "grozny-news.net", "if-news.ru", "ingushetiya-news.net", "irkutsk-news.net",
    "ivanovo-news.net", "izhevsk-news.net", "kaliningrad-news.net", "kalmykia-news.net",
    "kaluga-news.net", "kamchatka-news.net", "karelia-news.net", "kazan-news.net",
    "kemerovo-news.net", "khabarovsk-news.net", "kherson-news.ru", "khmelnitskiy-news.ru",
    "kiev-news.com.ua", "kirov-news.net", "kirovograd-news.ru", "komi-news.net",
    "kostroma-news.net", "kramatorsk-news.ru", "krasnodar-news.net", "krasnoyarsk-news.net",
    "kremenchug-news.ru", "krivoy-rog-news.ru", "kurgan-news.net", "kursk-news.net",
    "latvia-news.com", "lenta.donetsk.ua", "lenta.if.ua", "lenta.kharkiv.ua",
    "lenta.lviv.ua", "lenta.te.ua", "lipetsk-news.net", "lithuania-news.com",
    "lnr-news.ru", "lugansk-news.ru", "lvov-news.ru", "magadan-news.net",
    "mariel-news.net", "mariupol-news.ru", "melitopol-news.ru", "moldova-news.com",
    "moskva-news.com", "msk-news.net", "murmansk-news.net", "nabchelny-news.ru",
    "nalchik-news.net", "nao-news.net", "news-armenia.com", "news-balashiha.ru",
    "news-estonia.com", "news-kharkov.ru", "news-kiev.ru", "news-makeevka.ru",
    "news-odessa.ru", "news-pravda.com", "news-surgut.ru", "niknews.com.ua",
    "nikolaev-news.ru", "nikolaevnews.com.ua", "nikopol-news.ru", "nk-news.ru",
    "nn-news.net", "norilsk-news.ru", "novgorod-news.net", "novosibirsk-news.net",
    "novyny.kr.ua", "novyny.zt.ua", "omsk-news.net", "orel-news.net",
    "orenburg-news.net", "pavlograd-news.ru", "penza-news.net", "perm-news.net",
    "piter-news.net", "poltava-news.ru", "pravda-al.com", "pravda-ba.com",
    "pravda-bf.com", "pravda-bg.com", "pravda-cf.com", "pravda-cy.com",
    "pravda-cz.com", "pravda-de.com", "pravda-dk.com", "pravda-ee.com",
    "pravda-en.com", "pravda-es.com", "pravda-eu.online", "pravda-fi.com",
    "pravda-fr.com", "pravda-gr.com", "pravda-hr.com", "pravda-hu.com",
    "pravda-ie.com", "pravda-it.com", "pravda-jp.com", "pravda-ko.com",
    "pravda-lt.com", "pravda-lv.com", "pravda-md.com", "pravda-mk.com",
    "pravda-ne.com", "pravda-nl.com", "pravda-no.com", "pravda-pl.com",
    "pravda-pt.com", "pravda-ro.com", "pravda-rs.com", "pravda-se.com",
    "pravda-si.com", "pravda-sk.com", "pravda-tw.com", "pravda-ukraine.com",
    "pskov-news.net", "rostov-news.net", "rovno-news.ru", "ryazan-news.net",
    "sakhalin-news.net", "samara-news.net", "saransk-news.net", "saratov-news.net",
    "sevastopol-news.com", "slavyansk-news.ru", "smolensk-news.net", "sochi-news.net",
    "stavropol-news.net", "sumy-news.ru", "tagil-news.ru", "tambov-news.net",
    "ternopol-news.ru", "tiraspol-news.ru", "tolyatti-news.net", "tomsk-news.net",
    "topnews.ck.ua", "topnews.cn.ua", "topnews.cv.ua", "topnews.kiev.ua",
    "topnews.km.ua", "topnews.kr.ua", "topnews.ks.ua", "topnews.lg.ua",
    "topnews.odessa.ua", "topnews.pl.ua", "topnews.rv.ua", "topnews.sebastopol.ua",
    "topnews.sumy.ua", "topnews.uz.ua", "topnews.vn.ua", "topnews.volyn.ua",
    "topnews.zp.ua", "topnews.zt.ua", "tula-news.net", "tuva-news.net",
    "tver-news.net", "tyumen-news.net", "uanews.ck.ua", "uanews.cn.ua",
    "uanews.crimea.ua", "uanews.cv.ua", "uanews.donetsk.ua", "uanews.dp.ua",
    "uanews.if.ua", "uanews.kharkiv.ua", "uanews.km.ua", "uanews.kr.ua",
    "uanews.ks.ua", "uanews.lg.ua", "uanews.lviv.ua", "uanews.odessa.ua",
    "uanews.pl.ua", "uanews.rv.ua", "uanews.sumy.ua", "uanews.te.ua",
    "uanews.uz.ua", "uanews.vn.ua", "uanews.volyn.ua", "uanews.zp.ua",
    "uanews.zt.ua", "udmurt-news.net", "ufa-news.net", "ugra-news.net",
    "ulyanovsk-news.net", "ural-news.net", "uzhgorod-news.ru", "vin-news.ru",
    "vladikavkaz-news.net", "vladimir-news.net", "vladivostok-news.net", "volgograd-news.net",
    "vologda-news.net", "volyn-news.ru", "volzhskiy-news.ru", "voronezh-news.net",
    "yakutsk-news.net", "yamal-news.net", "yaroslavl-news.net", "zhitomir-news.ru",
    "zp-news.ru",
})
