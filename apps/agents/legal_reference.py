import re
import logging

logger = logging.getLogger(__name__)

# Controlled legal reference database
# Key format: (code, section)
# - code: "BNS" | "BNSS" | "BSA" | "Constitution" | etc.
# - section: section number/identifier string (e.g. "101", "Article 21")
VERIFIED_LEGAL_DATABASE = {
    # SUBSTANTIVE CRIMINAL LAW: BNS ↔ IPC
    ("BNS", "101"): {
        "code": "BNS",
        "section": "101",
        "title": "Punishment for murder",
        "legacy_code": "IPC",
        "legacy_section": "302",
        "type": "substantive",
        "statutory_text": "Whoever commits murder shall be punished with death or imprisonment for life, and shall also be liable to fine. Murder is defined as causing death with the intention of causing death or causing such bodily injury as the offender knows is likely to cause death."
    },
    ("BNS", "105"): {
        "code": "BNS",
        "section": "105",
        "title": "Culpable homicide not amounting to murder",
        "legacy_code": "IPC",
        "legacy_section": "304",
        "type": "substantive",
        "statutory_text": "Whoever commits culpable homicide not amounting to murder shall be punished with imprisonment for life or imprisonment of either description for a term which may extend to ten years, and shall also be liable to fine, if the act is done with the intention of causing death."
    },
    ("BNS", "109"): {
        "code": "BNS",
        "section": "109",
        "title": "Attempt to murder",
        "legacy_code": "IPC",
        "legacy_section": "307",
        "type": "substantive",
        "statutory_text": "Whoever does any act with such intention or knowledge, and under such circumstances that, if he by that act caused death, he would be guilty of murder, shall be punished with imprisonment of either description for a term which may extend to ten years, and shall also be liable to fine."
    },
    ("BNS", "114"): {
        "code": "BNS",
        "section": "114",
        "title": "Hurt",
        "legacy_code": "IPC",
        "legacy_section": "319",
        "type": "substantive",
        "statutory_text": "Whoever causes bodily pain, disease or infirmity to any person is said to cause hurt. It represents simple hurt without aggravating factors."
    },
    ("BNS", "115"): {
        "code": "BNS",
        "section": "115",
        "title": "Grievous hurt",
        "legacy_code": "IPC",
        "legacy_section": "320",
        "type": "substantive",
        "statutory_text": "Grievous hurt is defined as emasculation, permanent privation of the sight of either eye, permanent privation of the hearing of either ear, privation of any member or joint, destruction or permanent impairing of the powers of any member or joint, permanent disfiguration of the head or face, fracture or dislocation of a bone or tooth, or any hurt which endangers life or causes the sufferer to be in severe bodily pain or unable to follow his ordinary pursuits for twenty days."
    },
    ("BNS", "115(2)"): {
        "code": "BNS",
        "section": "115(2)",
        "title": "Voluntarily causing hurt",
        "legacy_code": "IPC",
        "legacy_section": "323",
        "type": "substantive",
        "statutory_text": "Whoever voluntarily causes hurt shall be punished with imprisonment of either description for a term which may extend to one year, or with fine which may extend to ten thousand rupees, or with both."
    },
    ("BNS", "117"): {
        "code": "BNS",
        "section": "117",
        "title": "Voluntarily causing grievous hurt by dangerous weapons or means",
        "legacy_code": "IPC",
        "legacy_section": "324",
        "type": "substantive",
        "statutory_text": "Whoever voluntarily causes grievous hurt by means of any instrument for shooting, stabbing or cutting, or any instrument which used as a weapon of offence is likely to cause death, shall be punished with imprisonment of either description for a term which may extend to ten years, and shall also be liable to fine."
    },
    ("BNS", "126"): {
        "code": "BNS",
        "section": "126",
        "title": "Wrongful restraint",
        "legacy_code": "IPC",
        "legacy_section": "339",
        "type": "substantive",
        "statutory_text": "Whoever voluntarily obstructs any person so as to prevent that person from proceeding in any direction in which that person has a right to proceed, is said wrongfully to restrain that person. Punishment involves imprisonment up to one month or fine up to five thousand rupees, or both."
    },
    ("BNS", "127"): {
        "code": "BNS",
        "section": "127",
        "title": "Wrongful confinement",
        "legacy_code": "IPC",
        "legacy_section": "340",
        "type": "substantive",
        "statutory_text": "Whoever wrongfully restrains any person in such a manner as to prevent that person from proceeding beyond certain circumscribing limits, is said wrongfully to confine that person. Punishment involves imprisonment up to one year or fine up to ten thousand rupees, or both."
    },
    ("BNS", "74"): {
        "code": "BNS",
        "section": "74",
        "title": "Assault or criminal force to woman with intent to outrage her modesty",
        "legacy_code": "IPC",
        "legacy_section": "354",
        "type": "substantive",
        "statutory_text": "Whoever assaults or uses criminal force to any woman, intending to outrage or knowing it to be likely that he will thereby outrage her modesty, shall be punished with imprisonment of either description for a term which shall not be less than one year but which may extend to five years, and shall also be liable to fine."
    },
    ("BNS", "63"): {
        "code": "BNS",
        "section": "63",
        "title": "Rape",
        "legacy_code": "IPC",
        "legacy_section": "376",
        "type": "substantive",
        "statutory_text": "A man is said to commit rape who has sexual intercourse with a woman under circumstances falling under any of the descriptions: against her will, without her consent, or with consent obtained by putting her in fear of death or hurt."
    },
    ("BNS", "303"): {
        "code": "BNS",
        "section": "303",
        "title": "Theft",
        "legacy_code": "IPC",
        "legacy_section": "379",
        "type": "substantive",
        "statutory_text": "Whoever, intending to take dishonestly any movable property out of the possession of any person without that person's consent, moves that property in order to such taking, is said to commit theft. Punishment involves imprisonment up to three years, or fine, or both."
    },
    ("BNS", "305"): {
        "code": "BNS",
        "section": "305",
        "title": "Theft in dwelling house, etc.",
        "legacy_code": "IPC",
        "legacy_section": "380",
        "type": "substantive",
        "statutory_text": "Whoever commits theft in any building, tent or vessel, which building, tent or vessel is used as a human dwelling, or for the custody of property, shall be punished with imprisonment of either description for a term which may extend to seven years, and shall also be liable to fine."
    },
    ("BNS", "309"): {
        "code": "BNS",
        "section": "309",
        "title": "Robbery",
        "legacy_code": "IPC",
        "legacy_section": "392",
        "type": "substantive",
        "statutory_text": "In all robbery there is either theft or extortion. Theft is robbery if, in order to the committing of the theft, or in committing the theft, or in carrying away property obtained by the theft, the offender, for that end, voluntarily causes or attempts to cause to any person death or hurt or wrongful restraint."
    },
    ("BNS", "310"): {
        "code": "BNS",
        "section": "310",
        "title": "Dacoity",
        "legacy_code": "IPC",
        "legacy_section": "395",
        "type": "substantive",
        "statutory_text": "When five or more persons conjointly commit or attempt to commit a robbery, or where the whole number of persons conjointly committing or attempting to commit a robbery, and persons present and aiding such commission or attempt, amount to five or more, every person so committing, attempting or aiding, is said to commit dacoity."
    },
    ("BNS", "318"): {
        "code": "BNS",
        "section": "318",
        "title": "Cheating",
        "legacy_code": "IPC",
        "legacy_section": "420",
        "type": "substantive",
        "statutory_text": "Whoever, by deceiving any person, fraudulently or dishonestly induces the person so deceived to deliver any property to any person, or to consent that any person shall retain any property, or intentionally induces the person so deceived to do or omit to do anything which he would not do or omit if he were not so deceived, is said to cheat."
    },
    ("BNS", "324"): {
        "code": "BNS",
        "section": "324",
        "title": "Mischief",
        "legacy_code": "IPC",
        "legacy_section": "425",
        "type": "substantive",
        "statutory_text": "Whoever, with intent to cause, or knowing that he is likely to cause, wrongful loss or damage to the public or to any person, causes the destruction of any property, or any such change in any property or in the situation thereof as destroys or diminishes its value or utility, or affects it injuriously, commits mischief."
    },
    ("BNS", "329"): {
        "code": "BNS",
        "section": "329",
        "title": "Criminal trespass",
        "legacy_code": "IPC",
        "legacy_section": "441",
        "type": "substantive",
        "statutory_text": "Whoever enters into or upon property in the possession of another with intent to commit an offence or to intimidate, insult or annoy any person in possession of such property, or having lawfully entered, remains there with intent to intimidate, insult or annoy, is said to commit criminal trespass."
    },
    ("BNS", "333"): {
        "code": "BNS",
        "section": "333",
        "title": "Punishment for criminal trespass",
        "legacy_code": "IPC",
        "legacy_section": "447",
        "type": "substantive",
        "statutory_text": "Whoever commits criminal trespass shall be punished with imprisonment of either description for a term which may extend to three months, or with fine which may extend to five thousand rupees, or with both."
    },
    ("BNS", "356"): {
        "code": "BNS",
        "section": "356",
        "title": "Defamation",
        "legacy_code": "IPC",
        "legacy_section": "499",
        "type": "substantive",
        "statutory_text": "Whoever, by words either spoken or intended to be read, or by signs or by visible representations, makes or publishes any imputation concerning any person intending to harm, or knowing or having reason to believe that such imputation will harm, the reputation of such person, is said to defame."
    },
    ("BNS", "351"): {
        "code": "BNS",
        "section": "351",
        "title": "Criminal intimidation",
        "legacy_code": "IPC",
        "legacy_section": "503",
        "type": "substantive",
        "statutory_text": "Whoever threatens another with any injury to his person, reputation or property, or to the person or reputation of any one in whom that person is interested, with intent to cause alarm to that person, or to cause that person to do any act which he is not legally bound to do, commits criminal intimidation."
    },
    ("BNS", "351(2)"): {
        "code": "BNS",
        "section": "351(2)",
        "title": "Punishment for criminal intimidation",
        "legacy_code": "IPC",
        "legacy_section": "506",
        "type": "substantive",
        "statutory_text": "Whoever commits the offence of criminal intimidation shall be punished with imprisonment of either description for a term which may extend to two years, or with fine, or with both."
    },

    # CRIMINAL PROCEDURE: BNSS ↔ CrPC
    ("BNSS", "35"): {
        "code": "BNSS",
        "section": "35",
        "title": "When police may arrest without warrant",
        "legacy_code": "CrPC",
        "legacy_section": "41",
        "type": "procedural",
        "statutory_text": "Outlines the powers of police to arrest without a warrant. It limits warrantless arrests primarily to cognizable offenses and specifies that arrests for offenses carrying less than seven years of imprisonment require the officer to satisfy necessity criteria (e.g. prevention of evidence tampering, flight risk, or threat to witnesses)."
    },
    ("BNSS", "43"): {
        "code": "BNSS",
        "section": "43",
        "title": "Arrest how made",
        "legacy_code": "CrPC",
        "legacy_section": "46",
        "type": "procedural",
        "statutory_text": "Defines the exact physical procedure for executing an arrest. It mandates that in making an arrest, the police officer shall actually touch or confine the body of the person to be arrested, and contains protections regarding female arrestees (e.g. arrests must be made by a female officer, and not during night hours without judicial permission)."
    },
    ("BNSS", "47"): {
        "code": "BNSS",
        "section": "47",
        "title": "Person arrested to be informed of grounds of arrest and of right to bail",
        "legacy_code": "CrPC",
        "legacy_section": "50",
        "type": "procedural",
        "statutory_text": "Mandates that every police officer or other person arresting any person without warrant shall forthwith communicate to him full particulars of the offence for which he is arrested or other grounds for such arrest, and if the offence is bailable, that he is entitled to bail."
    },
    ("BNSS", "58"): {
        "code": "BNSS",
        "section": "58",
        "title": "Person arrested not to be detained more than twenty-four hours",
        "legacy_code": "CrPC",
        "legacy_section": "57",
        "type": "procedural",
        "statutory_text": "No police officer shall detain in custody a person arrested without warrant for a longer period than reasonable, and such period shall not, in the absence of a special order of a Magistrate under Section 187, exceed twenty-four hours exclusive of the time necessary for the journey."
    },
    ("BNSS", "80"): {
        "code": "BNSS",
        "section": "80",
        "title": "Person arrested to be brought before Court without delay",
        "legacy_code": "CrPC",
        "legacy_section": "76",
        "type": "procedural",
        "statutory_text": "Mandates that the police officer executing a warrant of arrest shall without unnecessary delay bring the person arrested before the Court, provided that such delay shall not exceed twenty-four hours exclusive of journey time."
    },
    ("BNSS", "187"): {
        "code": "BNSS",
        "section": "187",
        "title": "Procedure when investigation cannot be completed in twenty-four hours",
        "legacy_code": "CrPC",
        "legacy_section": "167",
        "type": "procedural",
        "statutory_text": "Governs judicial remand and police custody. If investigation cannot be completed within 24 hours, the police must produce the accused before a Magistrate, who may authorize detention in police or judicial custody for a cumulative term not exceeding fifteen days or more depending on offense severity."
    },
    ("BNSS", "173"): {
        "code": "BNSS",
        "section": "173",
        "title": "Information in cognizable cases (FIR)",
        "legacy_code": "CrPC",
        "legacy_section": "154",
        "type": "procedural",
        "statutory_text": "Governs the registration of a First Information Report (FIR) for cognizable offences. It mandates that every information relating to the commission of a cognizable offence shall be reduced to writing by the officer in charge of a police station and signed by the informant."
    },
    ("BNSS", "174"): {
        "code": "BNSS",
        "section": "174",
        "title": "Information as to non-cognizable cases and investigation of such cases",
        "legacy_code": "CrPC",
        "legacy_section": "155",
        "type": "procedural",
        "statutory_text": "Governs police response to non-cognizable reports. The officer enters the substance in the diary and refers the informant to the Magistrate. Police cannot investigate a non-cognizable case without a Magistrate's order."
    },
    ("BNSS", "175"): {
        "code": "BNSS",
        "section": "175",
        "title": "Police officer's power to investigate cognizable case",
        "legacy_code": "CrPC",
        "legacy_section": "156",
        "type": "procedural",
        "statutory_text": "Empowers any officer in charge of a police station to investigate any cognizable case without the order of a Magistrate, and outlines the broad scope of police investigative authority."
    },
    ("BNSS", "175(3)"): {
        "code": "BNSS",
        "section": "175(3)",
        "title": "Magistrate's power to order investigation (FIR registration)",
        "legacy_code": "CrPC",
        "legacy_section": "156(3)",
        "type": "procedural",
        "statutory_text": "Any Magistrate empowered under Section 210 may order an investigation by the police into a cognizable offense, which is the primary legal remedy for registering an FIR when local police refuse to do so."
    },
    ("BNSS", "176"): {
        "code": "BNSS",
        "section": "176",
        "title": "Procedure for investigation",
        "legacy_code": "CrPC",
        "legacy_section": "157",
        "type": "procedural",
        "statutory_text": "Outlines the steps police must take to start an investigation, including sending a report to the Magistrate and proceeding to the spot to investigate the facts and circumstances of the case."
    },
    ("BNSS", "180"): {
        "code": "BNSS",
        "section": "180",
        "title": "Examination of witnesses by police",
        "legacy_code": "CrPC",
        "legacy_section": "161",
        "type": "procedural",
        "statutory_text": "Empowers investigating police officers to examine orally any person supposed to be acquainted with the facts and circumstances of the case, and reduce their statements to writing."
    },
    ("BNSS", "183"): {
        "code": "BNSS",
        "section": "183",
        "title": "Recording of confessions and statements",
        "legacy_code": "CrPC",
        "legacy_section": "164",
        "type": "procedural",
        "statutory_text": "Empowers Metropolitan or Judicial Magistrates to record confessions or statements made to them in the course of an investigation or at any time before inquiry or trial."
    },
    ("BNSS", "185"): {
        "code": "BNSS",
        "section": "185",
        "title": "Search by police officer",
        "legacy_code": "CrPC",
        "legacy_section": "166",
        "type": "procedural",
        "statutory_text": "Governs police search procedures. If an investigating officer has reasonable grounds to believe that things necessary for investigation are in a certain place, they may search that place after recording grounds."
    },
    ("BNSS", "193"): {
        "code": "BNSS",
        "section": "193",
        "title": "Report of police officer on completion of investigation (charge sheet)",
        "legacy_code": "CrPC",
        "legacy_section": "173",
        "type": "procedural",
        "statutory_text": "Mandates that every investigation must be completed without unnecessary delay, and outlines the final report (charge sheet/closure report) filed by the police before the Magistrate."
    },
    ("BNSS", "478"): {
        "code": "BNSS",
        "section": "478",
        "title": "In what cases bail to be taken",
        "legacy_code": "CrPC",
        "legacy_section": "436",
        "type": "procedural",
        "statutory_text": "Declares that when a person accused of a bailable offence is arrested or detained without warrant, they shall be released on bail as a matter of right if they are prepared to give bail."
    },
    ("BNSS", "480"): {
        "code": "BNSS",
        "section": "480",
        "title": "When bail may be taken in case of non-bailable offence",
        "legacy_code": "CrPC",
        "legacy_section": "437",
        "type": "procedural",
        "statutory_text": "Outlines the judicial discretion and criteria for granting bail in non-bailable offences, detailing restrictions for serious offences punishable with death or life imprisonment."
    },
    ("BNSS", "483"): {
        "code": "BNSS",
        "section": "483",
        "title": "Special powers of High Court or Court of Session regarding bail",
        "legacy_code": "CrPC",
        "legacy_section": "439",
        "type": "procedural",
        "statutory_text": "Grants concurrent jurisdiction and special discretionary powers to the High Court and Court of Session to release any accused person on bail and set bail conditions."
    },
    ("BNSS", "351"): {
        "code": "BNSS",
        "section": "351",
        "title": "Power to examine the accused",
        "legacy_code": "CrPC",
        "legacy_section": "313",
        "type": "procedural",
        "statutory_text": "Empowers the Court at any stage of inquiry or trial, without warning the accused, to put questions to him to explain any circumstances appearing in the evidence against him."
    },
    ("BNSS", "395"): {
        "code": "BNSS",
        "section": "395",
        "title": "Order to pay compensation",
        "legacy_code": "CrPC",
        "legacy_section": "357",
        "type": "procedural",
        "statutory_text": "Empowers the court when imposing a sentence of fine to order the fine to be applied in the payment to any person of compensation for loss or injury caused by the offense."
    },
    ("BNSS", "415"): {
        "code": "BNSS",
        "section": "415",
        "title": "Appeals from convictions",
        "legacy_code": "CrPC",
        "legacy_section": "374",
        "type": "procedural",
        "statutory_text": "Outlines the statutory right of appeal for any person convicted on a trial held by a High Court, Court of Session, or Magistrate."
    },
    ("BNSS", "210"): {
        "code": "BNSS",
        "section": "210",
        "title": "Cognizance of offences by Magistrates",
        "legacy_code": "CrPC",
        "legacy_section": "190",
        "type": "procedural",
        "statutory_text": "Outlines how a Magistrate may take cognizance of any offence: upon receiving a complaint of facts, upon a police report, or upon information received from any person other than a police officer."
    },
    ("BNSS", "223"): {
        "code": "BNSS",
        "section": "223",
        "title": "Examination of complainant",
        "legacy_code": "CrPC",
        "legacy_section": "200",
        "type": "procedural",
        "statutory_text": "Mandates that a Magistrate taking cognizance of an offence on complaint shall examine upon oath the complainant and the witnesses present, reducing the substance of the examination to writing."
    },
    ("BNSS", "528"): {
        "code": "BNSS",
        "section": "528",
        "title": "Saving of inherent powers of High Court",
        "legacy_code": "CrPC",
        "legacy_section": "482",
        "type": "procedural",
        "statutory_text": "Declares that nothing in this Sanhita shall limit or affect the inherent powers of the High Court to make such orders as may be necessary to give effect to any order under this Sanhita, or to prevent abuse of the process of any Court or otherwise to secure the ends of justice."
    },

    # EVIDENCE LAW: BSA ↔ Indian Evidence Act (IEA)
    ("BSA", "3"): {
        "code": "BSA",
        "section": "3",
        "title": "Definitions of Evidence",
        "legacy_code": "Indian Evidence Act",
        "legacy_section": "3",
        "type": "evidence",
        "statutory_text": "Defines what constitutes legal evidence in courts. It divides evidence into oral evidence (all statements which the Court permits or requires to be made before it by witnesses) and documentary evidence (all documents including electronic records produced for the inspection of the Court)."
    },
    ("BSA", "35"): {
        "code": "BSA",
        "section": "35",
        "title": "Relevancy of entry in public record or electronic record",
        "legacy_code": "Indian Evidence Act",
        "legacy_section": "35",
        "type": "evidence",
        "statutory_text": "States that an entry in any public or other official book, register or record, or an electronic record, stating a fact in issue or relevant fact, and made by a public servant in the discharge of his official duty, is itself a relevant fact."
    },
    ("BSA", "57"): {
        "code": "BSA",
        "section": "57",
        "title": "Admissibility of electronic records",
        "legacy_code": "Indian Evidence Act",
        "legacy_section": "65B",
        "type": "evidence",
        "statutory_text": "Governs the admissibility of digital and electronic evidence in court (formerly Section 65B of the Indian Evidence Act). It mandates that any information contained in an electronic record which is printed or recorded on computer media shall be deemed to be a document and shall be admissible in any proceedings, subject to verification and certificate requirements."
    },
    ("BSA", "61"): {
        "code": "BSA",
        "section": "61",
        "title": "Primary and Secondary Evidence",
        "legacy_code": "Indian Evidence Act",
        "legacy_section": "65",
        "type": "evidence",
        "statutory_text": "Outlines the rules for proving the contents of documents. It states that documents must be proved by primary evidence (the document itself produced for inspection) or by secondary evidence (certified copies, oral accounts of contents, or copies made by mechanical processes) under specific circumstances."
    },

    # CONSTITUTIONAL LAW
    ("CONSTITUTION", "ARTICLE 21"): {
        "code": "Constitution",
        "section": "Article 21",
        "title": "Protection of life and personal liberty",
        "legacy_code": None,
        "legacy_section": None,
        "type": "constitutional",
        "statutory_text": "No person shall be deprived of his life or personal liberty except according to procedure established by law. This constitutional guarantee protects against arbitrary state action, police brutality, and custody torture, and establishes a right to emergency medical care and access to justice."
    },
    ("CONSTITUTION", "ARTICLE 22"): {
        "code": "Constitution",
        "section": "Article 22",
        "title": "Protection against arrest and detention in certain cases",
        "legacy_code": None,
        "legacy_section": None,
        "type": "constitutional",
        "statutory_text": "Article 22 guarantees vital safeguards to any person arrested: the right to be informed of grounds of arrest as soon as possible, the right to consult and be defended by a legal practitioner of choice, and the right to be produced before the nearest Magistrate within 24 hours of arrest (excluding journey time)."
    },

    # CIVIL & REMEDIAL STATUTES
    ("SPECIFIC RELIEF ACT", "SECTION 6"): {
        "code": "Specific Relief Act",
        "section": "Section 6",
        "title": "Suit by person dispossessed of immovable property",
        "legacy_code": None,
        "legacy_section": None,
        "type": "civil",
        "statutory_text": "If any person is dispossessed without his consent of immovable property otherwise than in due course of law, he or any person claiming through him may, by suit, recover possession thereof. Such suit must be filed within six months from the date of dispossession."
    },
    ("SPECIFIC RELIEF ACT", "SECTION 38"): {
        "code": "Specific Relief Act",
        "section": "Section 38",
        "title": "Perpetual injunction when granted",
        "legacy_code": None,
        "legacy_section": None,
        "type": "civil",
        "statutory_text": "Subject to the other provisions contained in or referred to by this Chapter, a perpetual injunction may be granted to the plaintiff to prevent the breach of an obligation existing in his favour, whether expressly or by implication."
    },
    ("TRANSFER OF PROPERTY ACT", "SECTION 106"): {
        "code": "Transfer of Property Act",
        "section": "Section 106",
        "title": "Duration of certain leases in absence of written contract",
        "legacy_code": None,
        "legacy_section": None,
        "type": "civil",
        "statutory_text": "In the absence of a contract or local law or usage to the contrary, a lease of immovable property for agricultural or manufacturing purposes shall be deemed to be a lease from year to year, terminable, on the part of either lessor or lessee, by six months' notice; and a lease of immovable property for any other purpose shall be deemed to be a lease from month to month, terminable, on the part of either lessor or lessee, by fifteen days' notice."
    },
    ("CODE ON WAGES", "SECTION 45"): {
        "code": "Code on Wages",
        "section": "Section 45",
        "title": "Claims under this Code",
        "legacy_code": None,
        "legacy_section": None,
        "type": "civil",
        "statutory_text": "Enables workers or Inspectors-cum-Facilitators to file claims regarding unpaid salaries or defaults on minimum wages. Claims are filed before a designated statutory Authority (Labour Commissioner or Inspector-cum-Facilitator), rather than a police station or standard civil court."
    },
    ("CONSUMER PROTECTION ACT", "KEY PROVISIONS"): {
        "code": "Consumer Protection Act",
        "section": "Key Provisions",
        "title": "Unfair trade practices and consumer redressal",
        "legacy_code": None,
        "legacy_section": None,
        "type": "civil",
        "statutory_text": "Protects consumer rights regarding trade deficiencies, deceptive sales, online fraud, or contractual failure. Provides a three-tier dispute machinery: District Commission (claims up to 1 crore), State Commission (up to 10 crores), and National Commission."
    },
    ("LEGAL SERVICES ACT", "SECTION 12"): {
        "code": "Legal Services Act",
        "section": "Section 12",
        "title": "Criteria for giving legal services",
        "legacy_code": None,
        "legacy_section": None,
        "type": "civil",
        "statutory_text": "Section 12 prescribes eligibility for free legal aid, including women, children, members of SC/ST, industrial workmen, and persons in custody. Under this section, the District Legal Services Authority (DLSA) provides free legal assistance, panel advocates, and covers all filing expenses."
    },
    ("SUPREME COURT RULING", "PARMANAND KATARA"): {
        "code": "Supreme Court Ruling",
        "section": "Parmanand Katara",
        "title": "Right to Emergency Medical Treatment",
        "legacy_code": None,
        "legacy_section": None,
        "type": "constitutional",
        "statutory_text": "The Supreme Court of India in Parmanand Katara v. Union of India ruled that the preservation of human life is of paramount importance. Every hospital and doctor (government or private) has an absolute obligation to provide immediate emergency medical treatment to an injured person without delaying for police clearance or registration of a medico-legal case."
    },
    ("SUPREME COURT RULING", "DK BASU"): {
        "code": "Supreme Court Ruling",
        "section": "DK Basu",
        "title": "Arrest and custody guidelines",
        "legacy_code": None,
        "legacy_section": None,
        "type": "constitutional",
        "statutory_text": "Mandatory Supreme Court guidelines to prevent custody abuse: clear identification for arresting officers, preparation of an arrest memo signed by witnesses, notification to relative within 8-12 hours, mandatory medical checks, and access to counsel."
    },
}

def validate_legal_citation(code: str, section: str) -> dict:
    """
    Validates if the generated section belongs to the stated code
    and returns its verified record (including legacy cross-references).
    Supports prefix normalization ("45", "Section 45", "SECTION 45", "Article 21").
    """
    if not code or not section:
        return _unverified_record(code, section)
        
    code_clean = code.strip().upper()
    sec_raw = section.strip()
    sec_upper = sec_raw.upper()
    
    # Handle normalized names or aliases
    if code_clean == "BHARATIYA NYAYA SANHITA" or code_clean == "BNS":
        code_clean = "BNS"
    elif code_clean == "BHARATIYA NAGARIK SURAKSHA SANHITA" or code_clean == "BNSS":
        code_clean = "BNSS"
    elif code_clean == "BHARATIYA SAKSHYA ADHINIYAM" or code_clean == "BSA":
        code_clean = "BSA"
    elif code_clean == "CONSTITUTION OF INDIA" or code_clean == "CONSTITUTION":
        code_clean = "CONSTITUTION"
    elif code_clean == "SPECIFIC RELIEF ACT 1963" or code_clean == "SPECIFIC RELIEF ACT":
        code_clean = "SPECIFIC RELIEF ACT"
    elif code_clean == "TRANSFER OF PROPERTY ACT 1882" or code_clean == "TRANSFER OF PROPERTY ACT":
        code_clean = "TRANSFER OF PROPERTY ACT"
    elif code_clean == "CODE ON WAGES 2019" or code_clean == "CODE ON WAGES":
        code_clean = "CODE ON WAGES"
    elif code_clean == "CONSUMER PROTECTION ACT 2019" or code_clean == "CONSUMER PROTECTION ACT":
        code_clean = "CONSUMER PROTECTION ACT"
    elif code_clean == "LEGAL SERVICES AUTHORITIES ACT 1987" or code_clean == "LEGAL SERVICES ACT" or code_clean == "LEGAL SERVICES AUTHORITIES ACT":
        code_clean = "LEGAL SERVICES ACT"
    elif code_clean == "SUPREME COURT RULING":
        code_clean = "SUPREME COURT RULING"

    # Extract bare section identifier by stripping SECTION / ARTICLE prefixes
    sec_bare = re.sub(r'^(SECTION|SEC\.|SEC|ARTICLE|ART\.|ART)\s+', '', sec_upper, flags=re.IGNORECASE).strip()

    candidate_keys = [
        (code_clean, sec_upper),
        (code_clean, sec_bare),
        (code_clean, f"SECTION {sec_bare}"),
        (code_clean, f"ARTICLE {sec_bare}")
    ]

    record = None
    for k in candidate_keys:
        if k in VERIFIED_LEGAL_DATABASE:
            record = VERIFIED_LEGAL_DATABASE[k]
            break

    if record:
        return {
            "code": record["code"],
            "section": record["section"],
            "title": record["title"],
            "legacy_code": record.get("legacy_code"),
            "legacy_section": record.get("legacy_section"),
            "type": record["type"],
            "statutory_text": record["statutory_text"],
            "verified": True
        }
        
    return _unverified_record(code, section)


def _unverified_record(code: str, section: str) -> dict:
    return {
        "code": code,
        "section": section,
        "title": "Unverified legal provision",
        "legacy_code": None,
        "legacy_section": None,
        "type": "unknown",
        "statutory_text": "This legal provision could not be verified against the authoritative local database.",
        "verified": False
    }
