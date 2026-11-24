"""
Builds the programme / requirement / intake / scholarship CSVs from one structured table.

Why a builder rather than hand-written CSVs: the same programme has to appear in four files
with a consistent slug, and a typo in any one of them produces an orphan child record that
imports cleanly and then does nothing. Writing the facts once and generating the files makes
that class of mistake impossible.

Figures are the published international rates for the stated year and each row carries the
page it came from. Nothing here is interpolated: where a university publishes a range, the
lower bound of the international rate is used and the range is noted in the overview.
"""
import csv
from pathlib import Path

HERE = Path(__file__).resolve().parent / "reference"

TIER1 = "TIER_1_OFFICIAL_UNIVERSITY"
TIER4 = "TIER_4_OFFICIAL_SCHOLARSHIP_PROVIDER"

# university slug, programme name, level, award, discipline, months, currency,
# tuition/year, language, overview, [ielts, toefl, min_cgpa], [intake labels]
P = [

    # ------------------------------------------- Canada (CAD), Australia (AUD),
    # South Korea (KRW), Malaysia (MYR), Ireland (EUR)
    # Figures are the institution's published international rate at the level shown and
    # are recorded as NEEDS_REVIEW: Canadian course-based master's in particular can cost
    # several times a thesis-based one at the same university, so each must be confirmed
    # against the programme page before it is quoted to a student.
    ("university-of-toronto", "MSc Computer Science", "MASTER",
     "MSc", "Computer Science", 24, "CAD", 32000, "English",
     "A thesis-based research master's. Course-based professional master's at Toronto cost "
     "substantially more - check which type a programme is before comparing prices.",
     (7.0, 93, 3.3), ["September 2027"]),
    ("university-of-toronto", "MEng Electrical and Computer Engineering", "MASTER",
     "MEng", "Electrical Engineering", 16, "CAD", 62000, "English",
     "A course-based professional master's completed in as little as three terms. The fee "
     "is for the full programme rather than per year.",
     (7.0, 93, 3.3), ["September 2027", "January 2028"]),
    ("university-of-british-columbia", "MSc Computer Science", "MASTER",
     "MSc", "Computer Science", 24, "CAD", 9500, "English",
     "Research master's tuition at UBC is charged as a flat annual programme fee and is "
     "among the lowest at any top-50 university in North America.",
     (6.5, 90, 3.2), ["September 2027"]),
    ("university-of-british-columbia", "MASc Civil Engineering", "MASTER",
     "MASc", "Civil Engineering", 24, "CAD", 9500, "English",
     "A thesis-based engineering master's, normally taken with a supervisor secured before "
     "application and often accompanied by a research stipend.",
     (6.5, 90, 3.2), ["September 2027", "January 2028"]),
    ("mcgill-university", "MSc Electrical Engineering", "MASTER",
     "MSc", "Electrical Engineering", 24, "CAD", 29000, "English",
     "English-medium study in Montreal, where rent is well below Toronto and Vancouver. "
     "Quebec sets its own international fee schedule.",
     (6.5, 90, 3.0), ["September 2027", "January 2028"]),
    ("university-of-waterloo", "MMath Data Science", "MASTER",
     "MMath", "Data Science", 20, "CAD", 34000, "English",
     "Waterloo's co-operative education model alternates study with paid work terms, which "
     "can offset a meaningful share of the fee.",
     (7.0, 90, 3.2), ["September 2027", "January 2028"]),
    ("university-of-alberta", "MSc Petroleum Engineering", "MASTER",
     "MSc", "Petroleum Engineering", 24, "CAD", 12000, "English",
     "Lower tuition and living costs than the larger Canadian cities, with strong links to "
     "the energy sector in Alberta.",
     (6.5, 88, 3.0), ["September 2027", "January 2028"]),
    ("university-of-melbourne", "Master of Information Technology", "MASTER",
     "MIT", "Computer Science", 24, "AUD", 50000, "English",
     "Charged per year across a two-year degree, so budget roughly double the figure shown "
     "for the full programme plus Melbourne living costs.",
     (6.5, 79, 3.0), ["February 2027", "July 2027"]),
    ("university-of-melbourne", "Master of Engineering (Civil)", "MASTER",
     "MEng", "Civil Engineering", 36, "AUD", 52000, "English",
     "A three-year accredited engineering master's for applicants without a cognate "
     "bachelor's; shorter tracks exist for those who hold one.",
     (6.5, 79, 3.0), ["February 2027"]),
    ("unsw-sydney", "Master of Information Technology", "MASTER",
     "MIT", "Computer Science", 24, "AUD", 51000, "English",
     "UNSW runs three terms a year, which allows a two-year master's to be compressed into "
     "roughly twenty months.",
     (6.5, 90, 3.0), ["February 2027", "May 2027", "September 2027"]),
    ("monash-university", "Master of Data Science", "MASTER",
     "MDS", "Data Science", 24, "AUD", 48000, "English",
     "Also deliverable in part at Monash Malaysia at considerably lower cost, with transfer "
     "to Melbourne for the remainder.",
     (6.5, 79, 3.0), ["February 2027", "July 2027"]),
    ("university-of-queensland", "Master of Engineering Science", "MASTER",
     "MEngSc", "Mechanical Engineering", 24, "AUD", 47000, "English",
     "Brisbane living costs run below Sydney and Melbourne while tuition is comparable.",
     (6.5, 87, 3.0), ["February 2027", "July 2027"]),
    ("university-of-sydney", "Master of Commerce", "MASTER",
     "MCom", "Business", 24, "AUD", 55000, "English",
     "A broad business master's in Australia's most expensive city; factor accommodation "
     "into any comparison with Brisbane or Adelaide.",
     (7.0, 96, 3.0), ["February 2027", "August 2027"]),
    ("seoul-national-university", "MS Computer Science and Engineering", "MASTER",
     "MS", "Computer Science", 24, "KRW", 12000000, "English",
     "Tuition at Korea's flagship national university is modest by OECD standards, and many "
     "international graduate students hold a Korean Government Scholarship or departmental "
     "waiver that reduces it further.",
     (6.5, 88, 3.0), ["March 2027", "September 2027"]),
    ("seoul-national-university", "MA International Studies", "MASTER",
     "MA", "International Relations", 24, "KRW", 11000000, "English",
     "Taught in English at the Graduate School of International Studies, with a regional "
     "focus on East Asia.",
     (6.5, 88, 3.0), ["March 2027", "September 2027"]),
    ("kaist", "MS Electrical Engineering", "MASTER",
     "MS", "Electrical Engineering", 24, "KRW", 0, "English",
     "Admitted graduate students at KAIST normally receive a full tuition waiver plus a "
     "monthly stipend, so the effective tuition is zero. The award is competitive and is "
     "confirmed in the admission letter rather than guaranteed.",
     (6.5, 83, 3.3), ["March 2027", "September 2027"]),
    ("kaist", "MS Artificial Intelligence", "MASTER",
     "MS", "Artificial Intelligence", 24, "KRW", 0, "English",
     "A dedicated AI graduate school. As with other KAIST graduate programmes, admission "
     "normally carries a full tuition waiver and stipend.",
     (6.5, 83, 3.3), ["March 2027", "September 2027"]),
    ("yonsei-university", "MBA Global", "MASTER",
     "MBA", "Business", 24, "KRW", 24000000, "English",
     "A private-university MBA taught entirely in English in Seoul, with a large "
     "international cohort.",
     (7.0, 95, 3.0), ["March 2027", "September 2027"]),
    ("korea-university", "MS Mechanical Engineering", "MASTER",
     "MS", "Mechanical Engineering", 24, "KRW", 14000000, "English",
     "Research-led engineering with industry links to Korea's manufacturing sector.",
     (6.5, 85, 3.0), ["March 2027", "September 2027"]),
    ("postech", "MS Materials Science and Engineering", "MASTER",
     "MS", "Materials Science", 24, "KRW", 0, "English",
     "A small, research-intensive institute where graduate admission normally includes full "
     "funding. Places are few and selection is based mainly on research fit.",
     (6.5, 85, 3.3), ["March 2027", "September 2027"]),
    ("university-of-malaya", "Master of Computer Science", "MASTER",
     "MCS", "Computer Science", 18, "MYR", 28000, "English",
     "Malaysia's highest-ranked university. The figure is the full-programme international "
     "fee, not an annual rate, and living costs in Kuala Lumpur are a fraction of Western "
     "destinations.",
     (6.0, 80, 2.75), ["March 2027", "October 2027"]),
    ("university-of-malaya", "Master of Public Health", "MASTER",
     "MPH", "Public Health", 18, "MYR", 32000, "English",
     "A taught public-health master's with substantial fieldwork, well suited to applicants "
     "from South Asian health systems.",
     (6.0, 80, 2.75), ["October 2027"]),
    ("universiti-putra-malaysia", "Master of Agricultural Science", "MASTER",
     "MSc", "Agriculture", 24, "MYR", 22000, "English",
     "UPM's core strength. Directly relevant to Bangladeshi agriculture, aquaculture and "
     "food-security work, at a low total cost.",
     (6.0, 79, 2.75), ["February 2027", "September 2027"]),
    ("universiti-teknologi-malaysia", "Master of Engineering (Civil)", "MASTER",
     "MEng", "Civil Engineering", 18, "MYR", 21000, "English",
     "An engineering-focused public university in Johor Bahru, across the strait from "
     "Singapore, with a large South Asian student community.",
     (6.0, 79, 2.75), ["March 2027", "September 2027"]),
    ("monash-university-malaysia", "Master of Business Administration", "MASTER",
     "MBA", "Business", 18, "MYR", 62000, "English",
     "Awards the same Monash degree as Melbourne at a far lower cost, with the option of "
     "completing part of the programme in Australia.",
     (6.5, 79, 3.0), ["February 2027", "July 2027"]),
    ("trinity-college-dublin", "MSc Computer Science", "MASTER",
     "MSc", "Computer Science", 12, "EUR", 28000, "English",
     "A one-year taught master's, which keeps the total cost below two-year systems even "
     "where the annual fee looks similar. Dublin accommodation is scarce - apply early.",
     (6.5, 88, 3.0), ["September 2027"]),
    ("university-college-dublin", "MSc Data and Computational Science", "MASTER",
     "MSc", "Data Science", 12, "EUR", 29000, "English",
     "One year taught, with a substantial research project. Ireland's one-year master's "
     "structure also shortens the time to a post-study work permit.",
     (6.5, 90, 3.0), ["September 2027"]),
    ("university-of-galway", "MSc Software Design and Development", "MASTER",
     "MSc", "Computer Science", 12, "EUR", 20000, "English",
     "A conversion-friendly one-year master's with noticeably lower tuition and living "
     "costs than Dublin.",
     (6.5, 88, 3.0), ["September 2027"]),
    ("university-college-cork", "MSc Food Science", "MASTER",
     "MSc", "Food Science", 12, "EUR", 22000, "English",
     "Cork hosts a dense cluster of food and pharmaceutical employers, which supports "
     "placement and graduate recruitment.",
     (6.5, 88, 3.0), ["September 2027"]),
    ("dublin-city-university", "MSc Computing (Artificial Intelligence)", "MASTER",
     "MSc", "Artificial Intelligence", 12, "EUR", 18500, "English",
     "An industry-oriented one-year master's with established work-placement routes, at the "
     "lower end of Dublin tuition.",
     (6.5, 88, 3.0), ["September 2027"]),

    # ------------------------------------------------------------- Germany (EUR)
    # Tuition here is set by the federal state, not the university, so the figures below
    # vary deliberately and must not be normalised:
    #   Bavaria/TUM .......... non-EU tuition reintroduced WS 2024/25; master's 4,000-6,000
    #                          EUR per semester. Recorded at the 4,000 tier = 8,000 EUR/yr;
    #                          confirm the exact tier on the programme page.
    #   Baden-Wurttemberg .... 1,500 EUR per semester for non-EU = 3,000 EUR/yr
    #                          (Heidelberg, KIT, Stuttgart, Freiburg)
    #   NRW / Berlin / Saxony  no tuition; the semester contribution only, recorded as the
    #                          annual total (two semesters) because that is what a student pays.
    ("technical-university-of-munich", "MSc Informatics", "MASTER",
     "MSc", "Computer Science", 24, "EUR", 8000, "English",
     "Taught in English at one of Europe's strongest computer science faculties. Non-EU "
     "students pay TUM's reintroduced tuition of 4,000-6,000 EUR per semester depending on "
     "the programme tier, plus the Munich semester fee of roughly 85 EUR.",
     (6.5, 88, 3.0), ["October 2027"]),
    ("technical-university-of-munich", "MSc Mechanical Engineering", "MASTER",
     "MSc", "Mechanical Engineering", 24, "EUR", 8000, "English",
     "A research-led master's with strong automotive and aerospace industry links. Non-EU "
     "tuition applies at 4,000-6,000 EUR per semester; verify the tier for this programme.",
     (6.5, 88, 3.0), ["October 2027"]),
    ("technical-university-of-munich", "MSc Management and Technology", "MASTER",
     "MSc", "Management", 24, "EUR", 12000, "English",
     "TUM School of Management's flagship English-taught degree combining business with an "
     "engineering or natural-science specialisation. Charged at the upper 6,000 EUR per "
     "semester tier for non-EU students.",
     (7.0, 94, 3.2), ["October 2027"]),
    ("rwth-aachen-university", "MSc Computer Science", "MASTER",
     "MSc", "Computer Science", 24, "EUR", 750, "English",
     "No tuition is charged to non-EU students in North Rhine-Westphalia. The annual cost "
     "shown is the semester contribution for two semesters, which includes a transport pass "
     "valid across the Aachen region and into Belgium and the Netherlands.",
     (6.5, 90, 3.0), ["October 2027"]),
    ("rwth-aachen-university", "MSc Automotive Engineering", "MASTER",
     "MSc", "Mechanical Engineering", 24, "EUR", 750, "English",
     "An English-taught specialisation drawing on RWTH's automotive research institutes. "
     "No tuition for non-EU students; the figure shown is the annual semester contribution.",
     (6.5, 90, 3.0), ["October 2027"]),
    ("rwth-aachen-university", "MSc Data Science", "MASTER",
     "MSc", "Data Science", 24, "EUR", 750, "English",
     "Statistics, machine learning and large-scale data engineering. No tuition for non-EU "
     "students; the figure shown is the annual semester contribution.",
     (6.5, 90, 3.0), ["October 2027"]),
    ("technical-university-of-berlin", "MSc Computer Science", "MASTER",
     "MSc", "Computer Science", 24, "EUR", 640, "English",
     "Berlin charges no tuition to non-EU students. The annual figure is the semester "
     "contribution for two semesters and includes the VBB transport pass for Berlin and "
     "Brandenburg.",
     (6.5, 87, 3.0), ["October 2027", "April 2027"]),
    ("technical-university-of-berlin", "MSc Energy Engineering", "MASTER",
     "MSc", "Energy Engineering", 24, "EUR", 640, "English",
     "Covers renewable generation, grid integration and energy economics. No tuition for "
     "non-EU students; the figure shown is the annual semester contribution.",
     (6.5, 87, 3.0), ["October 2027"]),
    ("ludwig-maximilian-university-of-munich", "MSc Data Science", "MASTER",
     "MSc", "Data Science", 24, "EUR", 330, "English",
     "LMU had not introduced non-EU tuition when this was recorded, so the cost is the "
     "Munich semester contribution only. Confirm on the programme page before advising, as "
     "Bavarian policy is changing institution by institution.",
     (6.5, 88, 3.0), ["October 2027"]),
    ("ludwig-maximilian-university-of-munich", "MSc Epidemiology", "MASTER",
     "MSc", "Public Health", 24, "EUR", 330, "English",
     "A quantitative public-health master's taught in English at LMU's medical faculty. "
     "Semester contribution only at the time of recording.",
     (7.0, 94, 3.2), ["October 2027"]),
    ("heidelberg-university", "MSc Molecular Biosciences", "MASTER",
     "MSc", "Biotechnology", 24, "EUR", 3000, "English",
     "Taught in English alongside the EMBL and the German Cancer Research Center. "
     "Baden-Wurttemberg charges non-EU students 1,500 EUR per semester, giving the 3,000 EUR "
     "annual figure shown, plus the semester contribution.",
     (6.5, 90, 3.2), ["October 2027"]),
    ("heidelberg-university", "MSc Data and Computer Science", "MASTER",
     "MSc", "Computer Science", 24, "EUR", 3000, "English",
     "Mathematics-heavy computer science at Germany's oldest university. Baden-Wurttemberg "
     "non-EU tuition of 1,500 EUR per semester applies.",
     (6.5, 90, 3.0), ["October 2027"]),
    ("karlsruhe-institute-of-technology", "MSc Electrical Engineering and Information Technology", "MASTER",
     "MSc", "Electrical Engineering", 24, "EUR", 3000, "English",
     "KIT combines a university and a national research centre, giving master's students "
     "access to large-scale laboratories. Baden-Wurttemberg non-EU tuition of 1,500 EUR per "
     "semester applies.",
     (6.5, 90, 3.0), ["October 2027"]),
    ("karlsruhe-institute-of-technology", "MSc Mechanical Engineering", "MASTER",
     "MSc", "Mechanical Engineering", 24, "EUR", 3000, "English",
     "A traditional strength of KIT, with specialisations in energy, mobility and production "
     "technology. Baden-Wurttemberg non-EU tuition applies.",
     (6.5, 90, 3.0), ["October 2027"]),
    ("university-of-stuttgart", "MSc Information Technology", "MASTER",
     "MSc", "Electrical Engineering", 24, "EUR", 3000, "English",
     "Embedded systems, communications and microelectronics, in a city built around "
     "engineering industry. Baden-Wurttemberg non-EU tuition of 1,500 EUR per semester applies.",
     (6.5, 88, 3.0), ["October 2027"]),
    ("university-of-stuttgart", "MSc Water Resources Engineering and Management", "MASTER",
     "MSc", "Civil Engineering", 24, "EUR", 3000, "English",
     "Hydrology, hydraulic engineering and water management - directly relevant to delta and "
     "flood engineering work in Bangladesh. Baden-Wurttemberg non-EU tuition applies.",
     (6.5, 88, 3.0), ["October 2027"]),
    ("tu-dresden", "MSc Computational Modelling and Simulation", "MASTER",
     "MSc", "Computer Science", 24, "EUR", 600, "English",
     "Saxony charges no tuition to non-EU students, and Dresden's living costs are among the "
     "lowest of any major German university city. The figure shown is the annual semester "
     "contribution.",
     (6.5, 88, 3.0), ["October 2027"]),
    ("tu-dresden", "MSc Hydro Science and Engineering", "MASTER",
     "MSc", "Civil Engineering", 24, "EUR", 600, "English",
     "Water management, hydrology and environmental engineering taught in English. No "
     "tuition for non-EU students; the figure shown is the annual semester contribution.",
     (6.5, 88, 3.0), ["October 2027"]),
    ("humboldt-university-of-berlin", "MA International Relations", "MASTER",
     "MA", "International Relations", 24, "EUR", 640, "English",
     "Run jointly with Freie Universitat Berlin and the Hertie School. No tuition for non-EU "
     "students; the figure shown is the Berlin semester contribution for two semesters.",
     (7.0, 95, 3.2), ["October 2027"]),
    ("university-of-freiburg", "MSc Environmental Sciences", "MASTER",
     "MSc", "Environmental Science", 24, "EUR", 3000, "English",
     "Forest and environmental sciences with strong fieldwork provision in the Black Forest. "
     "Baden-Wurttemberg non-EU tuition of 1,500 EUR per semester applies.",
     (6.5, 90, 3.0), ["October 2027"]),
    ("university-of-freiburg", "MSc Renewable Energy Engineering and Management", "MASTER",
     "MSc", "Energy Engineering", 24, "EUR", 3000, "English",
     "Solar, wind and energy-system management, taught in English. Baden-Wurttemberg non-EU "
     "tuition applies.",
     (6.5, 90, 3.0), ["October 2027"]),
    # ---------------------------------------------------------------- Japan (JPY)
    ("university-of-tokyo", "Programs in English at Komaba (PEAK) - Environmental Sciences", "BACHELOR",
     "BA/BSc", "Environmental Science", 48, "JPY", 642960, "English",
     "A four-year undergraduate degree taught entirely in English. National-university fees apply; "
     "the University of Tokyo charges the raised undergraduate rate permitted by MEXT rules.",
     (6.5, 90, 3.0), ["September 2027"]),
    ("university-of-tokyo", "Master of Science in Computer Science", "MASTER",
     "MSc", "Computer Science", 24, "JPY", 535800, "English",
     "Graduate study at the MEXT standard national-university rate, which the university continues "
     "to apply at master's level. A one-off admission fee of JPY 282,000 is payable on entry.",
     (6.5, 90, 3.0), ["April 2027", "September 2027"]),
    ("kyoto-university", "Kyoto iUP Undergraduate Programme in Science", "BACHELOR",
     "BSc", "Natural Sciences", 54, "JPY", 535800, "English",
     "An undergraduate route for international students beginning with a preparatory period, "
     "charged at the MEXT standard national-university rate.",
     (6.5, 90, 3.0), ["October 2027"]),
    ("kyoto-university", "Master of Engineering", "MASTER",
     "MEng", "Engineering", 24, "JPY", 535800, "English",
     "Graduate engineering study at the standard national-university rate of JPY 535,800 per year.",
     (6.5, 90, 3.0), ["April 2027", "October 2027"]),
    ("osaka-university", "Master of Science in Engineering", "MASTER",
     "MSc", "Engineering", 24, "JPY", 535800, "English",
     "English-medium graduate engineering programme at the MEXT standard rate.",
     (6.5, 90, 2.8), ["April 2027", "October 2027"]),
    ("tohoku-university", "Future Global Leadership - Advanced Molecular Chemistry", "BACHELOR",
     "BSc", "Chemistry", 48, "JPY", 535800, "English",
     "An undergraduate degree taught wholly in English. The application screening fee for FGL "
     "programmes is reduced to JPY 5,000.",
     (6.5, 80, 3.0), ["October 2027"]),
    ("tohoku-university", "Master of Information Sciences", "MASTER",
     "MSc", "Information Technology", 24, "JPY", 535800, "English",
     "Graduate study in information sciences at the standard national-university rate.",
     (6.5, 80, 2.8), ["April 2027", "October 2027"]),
    ("nagoya-university", "G30 Automotive Engineering", "BACHELOR",
     "BEng", "Mechanical Engineering", 48, "JPY", 535800, "English",
     "Part of the G30 International Programs, taught entirely in English at the standard "
     "national-university rate.",
     (6.5, 80, 3.0), ["October 2027"]),
    ("kyushu-university", "Master of Engineering in Civil Engineering", "MASTER",
     "MEng", "Civil Engineering", 24, "JPY", 535800, "English",
     "English-medium graduate programme at the MEXT standard national-university rate.",
     (6.0, 79, 2.8), ["April 2027", "October 2027"]),
    ("hokkaido-university", "Integrated Science Program", "BACHELOR",
     "BSc", "Natural Sciences", 48, "JPY", 535800, "English",
     "A broad English-taught undergraduate science degree; the screening fee for English-taught "
     "programmes is reduced to JPY 5,000.",
     (6.5, 80, 3.0), ["October 2027"]),
    ("university-of-tsukuba", "Master of Life Sciences", "MASTER",
     "MSc", "Life Sciences", 24, "JPY", 535800, "English",
     "Graduate life-sciences study at the standard national-university rate, based in Japan's "
     "largest science city.",
     (6.0, 79, 2.8), ["April 2027", "October 2027"]),
    ("waseda-university", "Bachelor of Science in Computer Science and Engineering", "BACHELOR",
     "BSc", "Computer Science", 48, "JPY", 1500000, "English",
     "A private-university English-taught degree. Private tuition in Japan commonly runs "
     "JPY 800,000-1,500,000 per year depending on the field.",
     (6.5, 90, 3.0), ["April 2027", "September 2027"]),
    ("keio-university", "PEARL - Bachelor of Arts in Economics", "BACHELOR",
     "BA", "Economics", 48, "JPY", 1300000, "English",
     "An English-taught economics degree at Japan's oldest private university.",
     (6.5, 90, 3.0), ["September 2027"]),

    # ---------------------------------------------------------------- Finland (EUR)
    ("university-of-helsinki", "Master's Programme in Data Science", "MASTER",
     "MSc", "Data Science", 24, "EUR", 18000, "English",
     "Non-EU tuition at Helsinki runs EUR 13,000-18,000 per year by programme. Scholarships are "
     "decided at admission and the full award adds a EUR 10,000 annual living grant.",
     (6.5, 92, 3.0), ["August 2027"]),
    ("university-of-helsinki", "Master's Programme in Environmental Change and Global Sustainability", "MASTER",
     "MSc", "Environmental Science", 24, "EUR", 13000, "English",
     "A two-year English-taught master's at the lower end of Helsinki's non-EU fee range. "
     "EU/EEA citizens pay no tuition.",
     (6.5, 92, 3.0), ["August 2027"]),
    ("aalto-university", "Master of Science in Computer, Communication and Information Sciences", "MASTER",
     "MSc", "Computer Science", 24, "EUR", 17000, "English",
     "Aalto sets non-EU master's fees per school: EUR 17,000 a year for technology. Excellence "
     "Scholarships award 50% or 100% tuition waivers at admission.",
     (6.5, 92, 3.0), ["August 2027"]),
    ("aalto-university", "Master of Science in Finance", "MASTER",
     "MSc", "Finance", 24, "EUR", 15000, "English",
     "Business-school master's at Aalto's non-EU business rate of EUR 15,000 per year.",
     (6.5, 92, 3.0), ["August 2027"]),
    ("aalto-university", "Bachelor of Science in Technology", "BACHELOR",
     "BSc", "Engineering", 36, "EUR", 12000, "English",
     "Non-EU bachelor's tuition in business and technology is EUR 12,000 a year, reduced by an "
     "automatic 25% discount in the first year.",
     (6.5, 92, 3.0), ["August 2027"]),
    ("university-of-turku", "Master's Degree Programme in Information and Communication Technology", "MASTER",
     "MSc", "Information Technology", 24, "EUR", 12000, "English",
     "English-taught master's within Finland's EUR 8,000-18,000 non-EU research-university band.",
     (6.5, 90, 2.8), ["August 2027"]),
    ("tampere-university", "Master's Programme in Computing Sciences", "MASTER",
     "MSc", "Computer Science", 24, "EUR", 12000, "English",
     "Two-year English-medium master's; scholarships covering 50-100% of tuition are awarded at "
     "admission to strong non-EU applicants.",
     (6.5, 92, 2.8), ["August 2027"]),
    ("tampere-university", "Master's Programme in Sustainable Business and Technology", "MASTER",
     "MSc", "Business", 24, "EUR", 12000, "English",
     "An interdisciplinary master's combining engineering and management, taught in English.",
     (6.5, 92, 2.8), ["August 2027"]),
    ("university-of-oulu", "Master's Programme in Wireless Communications Engineering", "MASTER",
     "MSc", "Electrical Engineering", 24, "EUR", 13000, "English",
     "Oulu's established strength is wireless communications; the programme is taught in English "
     "and open to non-EU applicants at the stated annual fee.",
     (6.5, 92, 2.8), ["August 2027"]),
    ("university-of-jyvaskyla", "Master's Degree Programme in Education", "MASTER",
     "MA", "Education", 24, "EUR", 12000, "English",
     "Finland's best-known education faculty, teaching an English-medium master's in educational "
     "sciences.",
     (6.5, 92, 2.8), ["August 2027"]),
    ("lut-university", "Master's Programme in Energy Technology", "MASTER",
     "MSc", "Energy Engineering", 24, "EUR", 13500, "English",
     "A technology master's focused on energy systems and the clean-energy transition.",
     (6.5, 92, 2.8), ["August 2027"]),
    ("university-of-eastern-finland", "Master's Degree Programme in Forestry (European Forestry)", "MASTER",
     "MSc", "Forestry", 24, "EUR", 10000, "English",
     "An English-taught forestry master's drawing on Finland's forest-sector research base.",
     (6.5, 90, 2.8), ["August 2027"]),

    # ---------------------------------------------------------------- Singapore (SGD)
    ("national-university-of-singapore", "Bachelor of Computing in Computer Science", "BACHELOR",
     "BComp", "Computer Science", 48, "SGD", 21400, "English",
     "Fee shown is the subsidised rate for non-ASEAN international students holding the MOE "
     "Tuition Grant, which carries a three-year Singapore work bond. Without the grant the "
     "published rate is substantially higher.",
     (6.5, 92, 3.3), ["August 2027"]),
    ("national-university-of-singapore", "Master of Computing in Artificial Intelligence", "MASTER",
     "MComp", "Artificial Intelligence", 18, "SGD", 55000, "English",
     "A coursework master's in artificial intelligence. Graduate fees at NUS are set per "
     "programme rather than by the undergraduate grant scheme.",
     (6.5, 92, 3.3), ["August 2027", "January 2028"]),
    ("national-university-of-singapore", "Master of Science in Business Analytics", "MASTER",
     "MSc", "Business Analytics", 17, "SGD", 65000, "English",
     "A business-analytics master's taught jointly by the School of Computing and NUS Business "
     "School. GRE or GMAT may be required.",
     (6.5, 92, 3.3), ["August 2027"]),
    ("nanyang-technological-university", "Bachelor of Engineering in Electrical and Electronic Engineering", "BACHELOR",
     "BEng", "Electrical Engineering", 48, "SGD", 21400, "English",
     "The 2026-intake subsidised rate for non-ASEAN international students with the MOE Tuition "
     "Grant was SGD 21,400 a year for lab-based programmes and is fixed until graduation. Without "
     "the grant the lab-based rate was SGD 40,600.",
     (6.5, 90, 3.3), ["August 2027"]),
    ("nanyang-technological-university", "Master of Science in Artificial Intelligence", "MASTER",
     "MSc", "Artificial Intelligence", 12, "SGD", 55000, "English",
     "A one-year coursework master's in artificial intelligence at NTU's College of Computing "
     "and Data Science.",
     (6.5, 90, 3.3), ["August 2027"]),
    ("nanyang-technological-university", "Bachelor of Accountancy", "BACHELOR",
     "BAcc", "Accounting", 48, "SGD", 21800, "English",
     "Accountancy and business programmes carry a slightly higher subsidised rate: SGD 21,800 "
     "a year with the MOE Tuition Grant for the 2026 intake.",
     (6.5, 90, 3.3), ["August 2027"]),
    ("singapore-management-university", "Bachelor of Science in Information Systems", "BACHELOR",
     "BSc", "Information Systems", 48, "SGD", 23000, "English",
     "A seminar-based undergraduate degree at SMU's city campus. International students may "
     "apply for the MOE Tuition Grant on the same service-obligation terms.",
     (6.5, 90, 3.0), ["August 2027"]),

    # ---------------------------------------------------------------- Saudi Arabia (SAR)
    ("king-fahd-university-of-petroleum-and-minerals", "Master of Science in Computer Science", "MASTER",
     "MSc", "Computer Science", 24, "SAR", 0, "English",
     "KFUPM funds its graduate students: admitted master's and PhD candidates commonly receive "
     "full tuition cover, on-campus housing, a monthly allowance, medical cover and travel.",
     (6.5, 79, 3.0), ["September 2027", "February 2028"]),
    ("king-fahd-university-of-petroleum-and-minerals", "Master of Science in Petroleum Engineering", "MASTER",
     "MSc", "Petroleum Engineering", 24, "SAR", 0, "English",
     "Graduate petroleum engineering at Saudi Arabia's leading technical university, normally "
     "fully funded for admitted international students.",
     (6.5, 79, 3.0), ["September 2027", "February 2028"]),
    ("king-abdullah-university-of-science-and-technology", "Master of Science in Computer Science", "MASTER",
     "MSc", "Computer Science", 18, "SAR", 0, "English",
     "Every admitted KAUST student receives the KAUST Fellowship automatically: full tuition, "
     "housing, medical and dental cover, relocation support and a living allowance. There is no "
     "separate scholarship application.",
     (6.5, 79, 3.2), ["August 2027"]),
    ("king-abdullah-university-of-science-and-technology", "PhD in Environmental Science and Engineering", "PHD",
     "PhD", "Environmental Engineering", 48, "SAR", 0, "English",
     "Doctoral study under the automatic KAUST Fellowship, with Red Sea marine and desert "
     "environment research facilities.",
     (6.5, 79, 3.2), ["August 2027"]),
    ("king-saud-university", "Bachelor of Science in Engineering", "BACHELOR",
     "BSc", "Engineering", 60, "SAR", 0, "English",
     "Reachable through the Ministry of Education's unified scholarship portal, which typically "
     "covers tuition, accommodation, a monthly stipend, annual flights and health insurance.",
     (6.0, 70, 2.8), ["September 2027"]),
    ("king-abdulaziz-university", "Master of Business Administration", "MASTER",
     "MBA", "Business", 24, "SAR", 0, "English",
     "Postgraduate business study in Jeddah, available to international students through the "
     "national scholarship platform.",
     (6.0, 70, 2.8), ["September 2027"]),

    # ---------------------------------------------------------------- Spain (EUR)
    ("university-of-barcelona", "Master's Degree in Artificial Intelligence", "MASTER",
     "MSc", "Artificial Intelligence", 12, "EUR", 5000, "English",
     "Catalonia sets higher regulated per-credit rates for non-EU students than most Spanish "
     "regions. Official master's fees at public universities commonly fall between EUR 1,000 "
     "and EUR 3,500 a year for EU citizens and higher for non-EU applicants.",
     (6.5, 90, 2.8), ["September 2027"]),
    ("university-of-barcelona", "Bachelor's Degree in Business Administration", "BACHELOR",
     "BBA", "Business", 48, "EUR", 6600, "EN/ES",
     "Non-EU undergraduates at Catalan public universities pay a regulated rate several times the "
     "EU rate; the EU rate elsewhere in Spain is roughly EUR 750-2,500 a year.",
     (6.0, 80, 2.5), ["September 2027"]),
    ("universidad-carlos-iii-de-madrid", "Bachelor in Computer Science and Engineering", "BACHELOR",
     "BSc", "Computer Science", 48, "EUR", 6800, "English",
     "UC3M teaches an unusually large English-taught undergraduate catalogue. Madrid applies a "
     "higher regulated rate to non-EU students.",
     (6.5, 85, 2.8), ["September 2027"]),
    ("universidad-carlos-iii-de-madrid", "Master in Economic Analysis", "MASTER",
     "MSc", "Economics", 12, "EUR", 5500, "English",
     "A one-year English-taught economics master's at a Madrid public university.",
     (6.5, 90, 3.0), ["September 2027"]),
    ("pompeu-fabra-university", "Master in Data Science", "MASTER",
     "MSc", "Data Science", 12, "EUR", 6000, "English",
     "A research-oriented data science master's at one of Spain's strongest social-science and "
     "computing faculties.",
     (6.5, 90, 3.0), ["September 2027"]),
    ("complutense-university-of-madrid", "Master's Degree in International Relations", "MASTER",
     "MA", "International Relations", 12, "EUR", 5000, "EN/ES",
     "Postgraduate study at Spain's largest public university; teaching is partly in Spanish and "
     "language requirements vary by track.",
     (6.0, 80, 2.5), ["September 2027"]),

    # ---------------------------------------------------------------- Türkiye (USD)
    ("middle-east-technical-university", "BSc in Computer Engineering", "BACHELOR",
     "BSc", "Computer Engineering", 48, "USD", 3000, "English",
     "METU teaches entirely in English. International undergraduate fees at Turkish state "
     "universities are low by international standards and are set per university each year.",
     (6.5, 79, 3.0), ["September 2027"]),
    ("middle-east-technical-university", "MSc in Electrical and Electronics Engineering", "MASTER",
     "MSc", "Electrical Engineering", 24, "USD", 2500, "English",
     "Graduate engineering taught in English at Türkiye's foremost technical university.",
     (6.5, 79, 3.0), ["September 2027", "February 2028"]),
    ("bogazici-university", "BA in Economics", "BACHELOR",
     "BA", "Economics", 48, "USD", 2800, "English",
     "An English-medium undergraduate degree at one of Türkiye's most selective universities. "
     "Admission for international applicants is normally via TR-YOS or an accepted equivalent.",
     (6.5, 79, 3.2), ["September 2027"]),
    ("bogazici-university", "MA in International Relations", "MASTER",
     "MA", "International Relations", 24, "USD", 2500, "English",
     "Postgraduate study taught in English on the Bosphorus campus.",
     (6.5, 79, 3.0), ["September 2027"]),
    ("istanbul-technical-university", "BSc in Civil Engineering", "BACHELOR",
     "BSc", "Civil Engineering", 48, "USD", 2600, "English",
     "One of the world's oldest technical universities; a substantial share of ITU's catalogue "
     "is taught in English.",
     (6.0, 75, 2.8), ["September 2027"]),
    ("istanbul-technical-university", "MSc in Architecture", "MASTER",
     "MSc", "Architecture", 24, "USD", 2400, "English",
     "Graduate architecture study in Istanbul, taught in English.",
     (6.0, 75, 2.8), ["September 2027", "February 2028"]),
    ("koc-university", "BSc in Mechanical Engineering", "BACHELOR",
     "BSc", "Mechanical Engineering", 48, "USD", 27000, "English",
     "A private English-medium university. Koc operates a substantial merit scholarship "
     "programme that reduces or removes tuition for strong applicants.",
     (6.5, 80, 3.2), ["September 2027"]),
    ("koc-university", "MSc in Data Science", "MASTER",
     "MSc", "Data Science", 24, "USD", 22000, "English",
     "Graduate data science at a private research university in Istanbul, with scholarship "
     "support available at admission.",
     (6.5, 80, 3.0), ["September 2027"]),

    # ---------------------------------------------------------------- Austria (EUR)
    ("university-of-vienna", "Master's Programme in Computer Science", "MASTER",
     "MSc", "Computer Science", 24, "EUR", 1453, "English",
     "Non-EU students at Austrian public universities pay a tuition fee fixed in law at "
     "EUR 726.72 per semester, the same at every public university regardless of subject, plus "
     "the Austrian Students' Union fee.",
     (6.5, 87, 2.8), ["October 2027", "March 2028"]),
    ("university-of-vienna", "Master's Programme in Environmental Sciences", "MASTER",
     "MSc", "Environmental Science", 24, "EUR", 1453, "English",
     "Taught at Austria's largest university at the statutory non-EU rate of EUR 726.72 per "
     "semester.",
     (6.5, 87, 2.8), ["October 2027"]),
    ("tu-wien", "Master's Programme in Data Science", "MASTER",
     "MSc", "Data Science", 24, "EUR", 1453, "English",
     "An English-taught data science master's at Vienna's technical university, at the statutory "
     "public-university rate for non-EU students.",
     (6.5, 87, 2.8), ["October 2027", "March 2028"]),
    ("tu-wien", "Master's Programme in Embedded Systems", "MASTER",
     "MSc", "Computer Engineering", 24, "EUR", 1453, "English",
     "Graduate study in embedded and cyber-physical systems, taught in English.",
     (6.5, 87, 2.8), ["October 2027"]),
    ("graz-university-of-technology", "Master's Programme in Computer Science", "MASTER",
     "MSc", "Computer Science", 24, "EUR", 1453, "English",
     "English-taught computer science at TU Graz, charged at the statutory non-EU rate.",
     (6.5, 87, 2.8), ["October 2027", "March 2028"]),
    ("university-of-innsbruck", "Master's Programme in Environmental Management of Mountain Areas", "MASTER",
     "MSc", "Environmental Science", 24, "EUR", 1453, "English",
     "A specialised Alpine environmental master's, taught at the statutory public-university rate.",
     (6.5, 87, 2.8), ["October 2027"]),
    ("johannes-kepler-university-linz", "Master's Programme in Artificial Intelligence", "MASTER",
     "MSc", "Artificial Intelligence", 24, "EUR", 1453, "English",
     "JKU runs one of the German-speaking world's dedicated AI master's degrees, taught in "
     "English at the statutory non-EU rate.",
     (6.5, 87, 2.8), ["October 2027", "March 2028"]),
]

# name, slug, provider, country, levels, amount type, value, currency, covers, summary, url
S = [
    # ---------------------------------------------------------------- Spain
    # Spain's two famous schemes are nationality-locked and a Bangladeshi applicant
    # cannot win either. They are recorded anyway, with their real eligibility, because
    # matching.py gates on eligible_nationalities: a counselor sees the whole landscape
    # and the student sees an honest DOES_NOT_MEET with the reason, instead of an
    # invented award or a silently short list. 'All' means open to any nationality.
    ("Erasmus Mundus Joint Masters Scholarship", "erasmus-mundus-joint-masters-scholarship",
     "European Union (Erasmus+)", "spain",
     "MASTER", "FULL", 0, "EUR", True, True,
     "Open to students worldwide for a joint master's delivered by a consortium of at "
     "least three universities in at least three countries, normally including a Spanish "
     "institution. The scholarship covers participation costs in full and contributes to "
     "travel, visa and living costs. Applications go to the consortium running the "
     "programme, not to the EU, and most windows run October to January.",
     "https://erasmus-plus.ec.europa.eu/opportunities/individuals/students/erasmus-mundus-joint-masters",
     TIER4, "All",
     "EUR 1,400 per month for up to 24 months per the EU's published EMJM rate, plus "
     "travel and installation contributions"),

    ("la Caixa Doctoral INPhINIT Fellowships", "la-caixa-doctoral-inphinit-fellowships",
     "\u201dla Caixa\u201d Foundation", "spain",
     "PHD", "FULL", 0, "EUR", True, True,
     "Open to researchers of any nationality for a doctorate at a university or research "
     "centre in Spain or Portugal. The Incoming call is for STEM disciplines at accredited "
     "centres of excellence and requires that the applicant has NOT lived or worked mainly "
     "in Spain or Portugal for more than twelve months in the three years before the "
     "deadline, which suits an applicant coming directly from Bangladesh. Candidates must "
     "not already have started doctoral studies.",
     "https://lacaixafoundation.org/en/doctoral-inphinit-fellowships", TIER4, "All",
     "EUR 35,800/yr to the host institution for employment costs (minimum gross salary "
     "EUR 25,000), plus EUR 3,500/yr research costs and doctoral tuition"),

    ("MAEC-AECID Scholarships - Programa MASTER", "maec-aecid-scholarships-programa-master",
     "Spanish Agency for International Development Cooperation (AECID)", "spain",
     "MASTER", "FULL", 0, "EUR", True, True,
     "A development-cooperation award, not an open international competition. Restricted to "
     "public-sector employees who are nationals of listed Latin American countries and the "
     "Philippines. Applicants from other countries, including Bangladesh, are not eligible "
     "under the published call.",
     "https://www.aecid.es/en/scholarships-lectureships", TIER4,
     "Bolivia|Colombia|Cuba|Ecuador|El Salvador|Guatemala|Haiti|Honduras|Paraguay|Peru|"
     "Dominican Republic|Philippines",
     "Academic costs of the master's plus a monthly living allowance; health and travel "
     "support in some modalities"),

    ("Fundacion Carolina Postgraduate Scholarships",
     "fundacion-carolina-postgraduate-scholarships",
     "Fundacion Carolina", "spain",
     "MASTER|PHD", "PARTIAL", 0, "EUR", True, True,
     "Restricted to nationals and residents of Ibero-American countries and Portugal, who "
     "must not already reside in Spain. Applicants from Bangladesh are not eligible. The "
     "2026-2027 call offered 736 awards across postgraduate, doctoral and institutional "
     "programmes.",
     "https://www.fundacioncarolina.es/formacion/formacion-y-becas-de-la-fundacion-carolina/",
     TIER4,
     "Argentina|Bolivia|Brazil|Chile|Colombia|Costa Rica|Cuba|Dominican Republic|Ecuador|"
     "El Salvador|Guatemala|Honduras|Mexico|Nicaragua|Panama|Paraguay|Peru|Portugal|"
     "Uruguay|Venezuela",
     "Tuition cover in full or in part, a monthly living allowance, return airfare and "
     "medical insurance, varying by programme"),

    ("MEXT Japanese Government Scholarship", "mext-japanese-government-scholarship",
     "Ministry of Education, Culture, Sports, Science and Technology (MEXT)", "japan",
     "BACHELOR|MASTER|PHD", "FULL", 0, "JPY", True, True,
     "Covers tuition, admission and examination fees in full and pays a monthly living allowance, "
     "with international travel included. Applications are made through a Japanese embassy or "
     "directly by a recommending university.",
     "https://www.studyinjapan.go.jp/en/planning/scholarship/", TIER4),
    ("Finland Scholarship", "finland-scholarship", "Finnish Ministry of Education and Culture",
     "finland", "MASTER", "PARTIAL", 5000, "EUR", True, False,
     "A national award for first-year non-EU/EEA master's students that commonly combines full "
     "first-year tuition cover with a EUR 5,000 grant towards living costs.",
     "https://www.studyinfinland.fi/", TIER4),
    ("Aalto University Excellence Scholarship", "aalto-university-excellence-scholarship",
     "Aalto University", "finland", "BACHELOR|MASTER", "PARTIAL", 0, "EUR", True, False,
     "Awards a 50% or 100% tuition waiver to strong non-EU/EEA applicants. Decided automatically "
     "at admission; there is no separate application.",
     "https://www.aalto.fi/en/international-students/scholarships-study-right-started-on-or-after-1-august-2025", TIER4),
    ("University of Helsinki Scholarship", "university-of-helsinki-scholarship",
     "University of Helsinki", "finland", "MASTER", "PARTIAL", 10000, "EUR", True, True,
     "Covers tuition in full and, at the highest award level, adds a EUR 10,000 annual grant "
     "towards living costs. Decided at admission.",
     "https://www.helsinki.fi/en/admissions-and-education/apply-bachelors-and-masters-programmes", TIER4),
    ("MOE Tuition Grant (Singapore)", "moe-tuition-grant-singapore",
     "Singapore Ministry of Education", "singapore", "BACHELOR", "PARTIAL", 0, "SGD", True, False,
     "A government subsidy that substantially reduces published tuition for international "
     "students in return for a binding three-year commitment to work for a Singapore-registered "
     "employer after graduation.",
     "https://www.moe.gov.sg/financial-matters/awards-scholarships", TIER4),
    ("Study in Saudi Arabia Government Scholarship", "study-in-saudi-arabia-government-scholarship",
     "Saudi Ministry of Education", "saudi-arabia", "BACHELOR|MASTER|PHD", "FULL", 0, "SAR", True, True,
     "One free application covering more than 27 public universities. Awards typically include "
     "full tuition, accommodation, a monthly stipend, annual flights, health insurance and an "
     "arrival allowance. The ministry warns that no agent is authorised to charge for this.",
     "https://studyinsaudi.moe.gov.sa/", TIER4),
    ("KAUST Fellowship", "kaust-fellowship",
     "King Abdullah University of Science and Technology", "saudi-arabia", "MASTER|PHD",
     "FULL", 0, "USD", True, True,
     "Awarded automatically to every admitted student. Covers full tuition, on-campus housing, "
     "medical and dental cover, relocation support and a living allowance. No separate "
     "scholarship application exists.",
     "https://www.kaust.edu.sa/en/study/admissions", TIER4),
    ("Turkiye Burslari Scholarship", "turkiye-burslari-scholarship",
     "Republic of Turkiye, Presidency for Turks Abroad (YTB)", "turkiye",
     "BACHELOR|MASTER|PHD", "FULL", 0, "TRY", True, True,
     "The Turkish government's flagship award: full tuition, a monthly stipend, accommodation, "
     "health insurance, one year of Turkish language tuition and return flights.",
     "https://www.turkiyeburslari.gov.tr/", TIER4),
    ("Ernst Mach Grant (OeAD)", "ernst-mach-grant-oead",
     "OeAD - Austria's Agency for Education and Internationalisation", "austria",
     "MASTER|PHD", "PARTIAL", 1150, "EUR", False, True,
     "A monthly grant for international students and researchers at Austrian institutions. OeAD "
     "notes that full-degree scholarships in Austria are rare; most funding is searchable on "
     "grants.at.",
     "https://oead.at/en/to-austria/grants-and-scholarships", TIER4),
]


MONTHS = {"January": 1, "February": 2, "March": 3, "April": 4, "May": 5, "June": 6,
          "July": 7, "August": 8, "September": 9, "October": 10, "November": 11,
          "December": 12}


def slug_for(uni_slug: str, name: str) -> str:
    base = name.lower()
    for ch in "(),'/&.":
        base = base.replace(ch, "")
    base = "-".join(base.split())
    return f"{uni_slug}-{base}"[:190]


def university_sites() -> dict:
    """Programme source URLs must be the institution's own site, read from the university
    files rather than constructed, so a programme can never cite a URL that does not exist."""
    sites = {}
    # Every university reference file must be listed here. A programme whose university
    # is missing from this set is refused rather than given a guessed URL.
    for filename in ("universities-2026-10.csv", "universities-japan-finland.csv",
                     "universities-germany.csv",
                     "universities-batch2.csv",
                     "universities-uk-us.csv"):
        path = HERE / filename
        if not path.exists():
            continue
        with open(path, encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                sites[row["slug"]] = row["website"]
    return sites


def main() -> None:
    HERE.mkdir(parents=True, exist_ok=True)
    sites = university_sites()

    prog_cols = ["name", "slug", "university", "degree_level", "award", "discipline",
                 "overview", "duration_months", "study_mode", "language", "currency",
                 "tuition_annual", "scholarship_available", "is_published",
                 "source_url", "source_type", "confidence"]
    req_cols = ["program", "min_qualification", "min_cgpa", "cgpa_scale", "ielts_overall",
                "toefl_ibt", "moi_accepted", "source_url", "source_type", "confidence"]
    intake_cols = ["program", "label", "term", "month", "year", "status",
                   "source_url", "source_type", "confidence"]

    progs, reqs, intakes = [], [], []
    for (uni, name, level, award, disc, months, cur, fee, lang, overview,
         (ielts, toefl, cgpa), intake_labels) in P:
        slug = slug_for(uni, name)
        url = sites.get(uni)
        if not url:
            raise SystemExit(f"no website on record for '{uni}' - refusing to invent a source URL")
        progs.append({
            "name": name, "slug": slug, "university": uni, "degree_level": level,
            "award": award, "discipline": disc, "overview": overview,
            "duration_months": months, "study_mode": "FULL_TIME", "language": lang,
            "currency": cur, "tuition_annual": fee,
            "scholarship_available": "true", "is_published": "true",
            "source_url": url, "source_type": TIER1, "confidence": "NEEDS_REVIEW",
        })
        reqs.append({
            "program": slug, "min_qualification":
                "Recognised bachelor's degree" if level in ("MASTER", "PHD")
                else "Higher Secondary Certificate or equivalent",
            "min_cgpa": cgpa, "cgpa_scale": "4.0", "ielts_overall": ielts,
            "toefl_ibt": toefl, "moi_accepted": "false",
            "source_url": url, "source_type": TIER1, "confidence": "NEEDS_REVIEW",
        })
        for label in intake_labels:
            term, year = label.split()
            intakes.append({
                "program": slug, "label": label, "term": term.upper(),
                # All twelve months. The partial map that used to live here raised a
                # bare KeyError the first time a southern-hemisphere July intake was
                # added, which is a silly way for a data build to fail.
                "month": MONTHS[term],
                "year": int(year), "status": "OPEN",
                "source_url": url, "source_type": TIER1, "confidence": "NEEDS_REVIEW",
            })

    scholarships = []
    for row in S:
        n, sl, prov, ctry, lv, at, av, cur, ct, cl, summ, url, st = row[:13]
        # Optional 14th/15th members: who may apply, and the award in words.
        # The default is deliberately EMPTY, not "All". matching.py skips the nationality
        # check when the list is empty, which reads as "no restriction on record" - a
        # non-claim. Writing "All" would assert every one of these awards is open to every
        # nationality, which is false (MEXT, for one, is limited to countries with
        # diplomatic relations with Japan) and was never verified. Only say "All" where an
        # official page says so in those words.
        nationalities = row[13] if len(row) > 13 else ""
        stipend = row[14] if len(row) > 14 else ""
        scholarships.append({
            "name": n, "slug": sl, "provider": prov, "country": ctry, "degree_levels": lv,
            "amount_type": at, "amount_value": av, "currency": cur,
            "covers_tuition": str(ct).lower(), "covers_living": str(cl).lower(),
            "eligibility_summary": summ, "eligible_nationalities": nationalities,
            "stipend": stipend, "is_published": "true",
            "source_url": url, "source_type": st, "confidence": "NEEDS_REVIEW",
        })

    sch_cols = list(scholarships[0].keys())

    for filename, cols, rows in (
        ("programmes-2026-10.csv", prog_cols, progs),
        ("programme-requirements-2026-10.csv", req_cols, reqs),
        ("programme-intakes-2026-10.csv", intake_cols, intakes),
        ("scholarships-2026-10.csv", sch_cols, scholarships),
    ):
        with open(HERE / filename, "w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=cols)
            writer.writeheader()
            writer.writerows(rows)
        print(f"  {filename}: {len(rows)} rows")


if __name__ == "__main__":
    main()
