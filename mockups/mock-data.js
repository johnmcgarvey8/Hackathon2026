window.GEO_MOCK_DATA = {
  cmsConnectors: [
    {
      id: "sitecore",
      type: "Sitecore",
      logo: "sitecore",
      summary: "Enterprise CMS for product and editorial pages",
      capabilities: ["Product pages", "Editorial pages", "Structured data"]
    },
    {
      id: "shopify",
      type: "Shopify",
      logo: "shopify",
      summary: "Commerce platform for product content and theme templates",
      capabilities: ["Product content", "Collection pages", "Theme metafields"]
    },
    {
      id: "azure-static",
      type: "Azure Static Web Apps",
      logo: "azure",
      summary: "Static website hosting for new landing pages and campaign microsites",
      capabilities: ["Landing pages", "Campaign microsites", "Preview environments"]
    }
  ],
  cmsChanges: {
    "day-rec-1": {
      summary: "Add a visible sourcing FAQ to the organic produce page. The product description and checkout content stay unchanged.",
      fields: [
        { name: "FAQ / Question", before: "", after: "Where does Daylesford organic produce come from?" },
        { name: "FAQ / Answer", before: "", after: "Our seasonal range is sourced from approved farms and growers, with availability changing through the year." }
      ]
    },
    "day-rec-2": {
      summary: "Replace the standalone Product object with a graph containing Product and FAQPage. The FAQ text must also appear on the page before publication.",
      fields: [
        { name: "Structured data / JSON-LD", format: "json", before: "{\"@type\":\"Product\",\"name\":\"Organic produce\"}", after: "{\n  \"@context\": \"https://schema.org\",\n  \"@graph\": [\n    {\"@type\": \"Product\", \"name\": \"Organic produce\"},\n    {\"@type\": \"FAQPage\", \"mainEntity\": [{\"@type\": \"Question\", \"name\": \"Where does Daylesford organic produce come from?\", \"acceptedAnswer\": {\"@type\": \"Answer\", \"text\": \"Our seasonal range is sourced from approved farms and growers, with availability changing through the year.\"}}]}\n  ]\n}" }
      ]
    },
    "day-rec-3": {
      summary: "Replace the page title and search description with sourcing-led copy. No visible product copy or structured data changes are included.",
      fields: [
        { name: "SEO / Page title", before: "Organic Produce | Daylesford", after: "Seasonal Organic Produce & Sourcing | Daylesford" },
        { name: "SEO / Meta description", before: "Explore organic produce from Daylesford.", after: "Explore Daylesford's seasonal organic produce, learn about approved farms and growers, and check the current range and delivery information." }
      ]
    },
    "day-rec-4": {
      summary: "Create a new unpublished landing-page concept. The headline and outline below are proposed copy, not a finished page; a copywriter and designer must complete the draft.",
      fields: [
        { name: "Page / Working title", before: "", after: "Discover the season with Daylesford organic boxes" },
        { name: "Page / Introduction", before: "", after: "Explore a seasonal selection of organic produce and find the box that suits your table. Review the current contents and delivery information before ordering." },
        { name: "Editorial / Section outline", before: "", after: "1. Seasonal box proposition\n2. Current contents and availability\n3. Approved sourcing information\n4. Gifting questions\n5. Delivery information\n6. Links to available boxes" },
        { name: "Design / Brief", before: "", after: "Design a responsive hero, a box-comparison section and an FAQ section using approved brand assets. Confirm product availability with merchandising." }
      ]
    },
    "day-rec-5": {
      summary: "Create an unpublished educational article concept. Proposed headline, introduction and section outline require copywriter, designer and brand review.",
      fields: [
        { name: "Article / Working title", before: "", after: "A guide to choosing seasonal organic produce" },
        { name: "Article / Introduction", before: "", after: "Choosing seasonal produce starts with knowing what is available now. Explore the current range, learn about approved sourcing information and plan how you will use and store your selection." },
        { name: "Editorial / Section outline", before: "", after: "1. Start with the current seasonal range\n2. Read the approved sourcing information\n3. Plan meals around what is available\n4. Check storage guidance for each product\n5. Explore relevant produce and box options" },
        { name: "Design / Brief", before: "", after: "Use approved seasonal photography and a readable article layout. Add links to the current range; do not invent storage advice or unsupported claims." }
      ]
    },
    "con-rec-1": {
      summary: "Replace the product-description paragraph with terrain and cushioning guidance. Product name, price, inventory and checkout fields stay unchanged.",
      fields: [
        { name: "Product / Description", before: "A responsive trail shoe for outdoor adventures.", after: "Designed for mixed trail terrain, with tested midsole cushioning and a secure fit for everyday trail sessions." }
      ]
    },
    "con-rec-2": {
      summary: "Add a visible fit FAQ and link customers to the size guide. No sizing claims are added beyond the approved sample guidance.",
      fields: [
        { name: "FAQ / Question", before: "", after: "How should the Trail Runner Pro fit?" },
        { name: "FAQ / Answer", before: "", after: "Choose your usual Contoso size for a secure trail fit. Review the size guide before ordering." }
      ]
    }
  },
  projects: [
    {
      id: "daylesford",
      name: "Daylesford",
      initials: "DA",
      colour: "#107c10",
      domain: "daylesford.example",
      scopeLabel: "Commerce and editorial",
      activeGoal: "Increase qualified AI referral traffic",
      health: "On track",
      latestScore: 62,
      latestDelta: "+8",
      scoreTrend: {
        startDate: "2026-08-16",
        scores: [48, 48.3, 48.8, 49.1, 49.2, 49.6, 50.2, 50.7, 51, 51.1, 51.6, 52.4, 53.2, 53.6, 53.8, 54.3, 54.9, 55.2, 55.4, 56, 56.8, 57.6, 57.9, 58.1, 58.7, 59.4, 60.1, 60.4, 60.7, 61.4, 62]
      },
      nextRun: "17 Sep 2026, 08:00",
      lastRun: "14 Sep 2026",
      clarity: {
        status: "connected",
        label: "Connected",
        projectName: "Daylesford Commerce",
        lastSync: "15 Sep 2026, 11:18",
        signals: ["AI referral sessions", "Product-page engagement", "FAQ expansion rate", "Dead-click clusters"]
      },
      cms: {
        type: "Sitecore",
        status: "connected",
        environment: "Staging",
        connection: "Daylesford Commerce SXA",
        lastSync: "15 Sep 2026, 10:42",
        scopes: ["Product pages", "Editorial pages", "Structured data"]
      },
      staticSite: {
        type: "Azure Static Web Apps",
        label: "Static website hosting",
        status: "connected",
        environment: "Preview environment",
        connection: "daylesford-geo-landing (West Europe)",
        endpoint: "https://daylesford-geo-landing.azurestaticapps.net",
        lastSync: "15 Sep 2026, 10:51",
        scopes: ["Landing pages", "Campaign microsites"]
      },
      iq: {
        status: "connected",
        label: "Brand context ready",
        lastRefresh: "15 Sep 2026, 10:58",
        sources: [
          {
            id: "day-word",
            type: "Word",
            name: "Daylesford Brand & Tone Guidelines.docx",
            location: "Microsoft 365 / Brand Governance",
            owner: "Brand team",
            version: "v4.2",
            updated: "12 Sep 2026",
            status: "current",
            sections: ["Tone of voice", "Organic claims", "Product naming", "Words to avoid"]
          },
          {
            id: "day-sp",
            type: "SharePoint",
            name: "Daylesford Brand Hub",
            location: "SharePoint / Marketing / Brand Hub",
            owner: "Marketing operations",
            version: "Published 15 Sep",
            updated: "15 Sep 2026",
            status: "current",
            sections: ["Approved product terminology", "Audience principles", "Claims library", "Content owners"]
          },
          {
            id: "day-landing",
            type: "Word",
            name: "Daylesford Landing Page Guidelines.docx",
            location: "Microsoft 365 / Brand Governance",
            owner: "Digital marketing",
            version: "v0.1 placeholder",
            updated: "15 Sep 2026",
            status: "current",
            placeholder: true,
            sections: ["Page structure", "Headline and hero copy", "Calls to action", "Measurement and tagging"]
          }
        ]
      },
      metrics: {
        opportunities: 7,
        awaitingReview: 4,
        activeRuns: 0,
        publishedThisMonth: 9
      },
      recommendations: [
        {
          id: "day-rec-1",
          priority: "High",
          rank: 1,
          category: "FAQ",
          title: "Add product-origin questions to the organic produce page",
          target: "/products/organic-produce",
          impact: "High",
          effort: "Low",
          confidence: "Medium",
          owner: "E-commerce content",
          status: "Ready for review",
          brandAlignment: "Aligned",
          brandSource: "Daylesford Brand & Tone Guidelines.docx, section 3.1",
          summary: "Answer high-intent questions about sourcing and seasonality using approved claims language.",
          proposedChange: "Add three concise FAQs covering sourcing, seasonal availability, and delivery handling.",
          verification: "Validate FAQ schema, review search evidence after the next scheduled run, and compare engaged sessions.",
          evidence: {
            webiq: "Four of five grounded discovery queries returned competitor pages with explicit sourcing FAQs.",
            clarity: "Synthetic signal: visitors who expand existing delivery FAQs spend 38% longer on the page.",
            iq: "Use 'grown and made with care' language; avoid unqualified sustainability superlatives."
          }
        },
        {
          id: "day-rec-2",
          priority: "High",
          rank: 2,
          category: "JSON-LD",
          title: "Expand Product and FAQ structured data",
          target: "/products/organic-produce",
          impact: "High",
          effort: "Medium",
          confidence: "Medium",
          owner: "SEO engineering",
          status: "Ready for review",
          brandAlignment: "Aligned",
          brandSource: "Daylesford Brand Hub, Approved product terminology",
          summary: "Bring visible product details and approved FAQs into a validated JSON-LD graph.",
          proposedChange: "Add Product, Offer, and FAQPage entities using only visible, approved page content.",
          verification: "Run schema validation, compare rendered page content, and re-run grounded citation evaluation.",
          evidence: {
            webiq: "Grounded comparison pages expose richer entity descriptions and FAQ relationships.",
            clarity: "Synthetic signal: product-detail interactions concentrate around provenance and availability.",
            iq: "Product names must match the approved commerce taxonomy and omit unsupported health claims."
          }
        },
        {
          id: "day-rec-3",
          priority: "Medium",
          rank: 3,
          category: "Title tag",
          title: "Clarify category and location intent in the title",
          target: "/products/organic-produce",
          impact: "Medium",
          effort: "Low",
          confidence: "Medium",
          owner: "SEO manager",
          status: "Proposed",
          brandAlignment: "Needs brand review",
          brandSource: "Daylesford Brand & Tone Guidelines.docx, section 2.4",
          summary: "Make the page purpose explicit while retaining the approved premium tone.",
          proposedChange: "Test a title that combines organic produce, delivery intent, and the Daylesford name.",
          verification: "Brand review, SERP truncation check, and comparison in the next recurring evidence run.",
          evidence: {
            webiq: "The current title is less explicit than titles returned for three grounded category queries.",
            clarity: "Synthetic signal: no page-level signal is needed for this low-risk metadata test.",
            iq: "Lead with the customer need; retain the full Daylesford name and avoid promotional punctuation."
          }
        },
        {
          id: "day-rec-4",
          priority: "Low",
          rank: 4,
          category: "Landing page",
          title: "Explore a seasonal organic-box landing page",
          target: "New content concept",
          impact: "Medium",
          effort: "High",
          confidence: "Low",
          owner: "Campaign content",
          status: "Discovery",
          brandAlignment: "Needs brand review",
          brandSource: "Daylesford Brand Hub, Audience principles",
          summary: "Create a focused destination for seasonal box discovery if demand is confirmed.",
          proposedChange: "Develop a content brief and wireframe, then route through copywriter and designer review.",
          verification: "Confirm demand, content ownership, product availability, design capacity, and measurement plan.",
          evidence: {
            webiq: "Two grounded queries reveal a content gap around seasonal box comparison and gifting.",
            clarity: "Synthetic signal: users move between category and delivery pages before conversion.",
            iq: "New campaign pages require brand, merchandising, copywriter, and design approval."
          }
        },
        {
          id: "day-rec-5",
          priority: "Low",
          rank: 5,
          category: "Blog post",
          title: "Draft a guide to choosing seasonal produce",
          target: "New editorial concept",
          impact: "Medium",
          effort: "High",
          confidence: "Low",
          owner: "Campaign content",
          status: "Discovery",
          brandAlignment: "Needs brand review",
          brandSource: "Daylesford Brand & Tone Guidelines.docx, Tone of voice",
          summary: "Explore an educational article that answers seasonal-selection questions.",
          proposedChange: "Draft outline: introduce seasonal choice, explain approved sourcing facts, suggest storage questions, and link to relevant products. Copywriter to supply the final prose and designer to review presentation.",
          verification: "Confirm claims, approve copy and design, then require a separate publication decision.",
          evidence: {
            webiq: "Synthetic discovery evidence includes questions about selecting and storing seasonal produce.",
            clarity: "Illustrative content exploration signal; no measured traffic uplift is claimed.",
            iq: "Use informative language, verify every sourcing claim, and route editorial content through brand review."
          }
        }
      ],
      runs: [
        { id: "RUN-1048", goal: "Increase qualified AI referral traffic", scope: "daylesford.example/products/organic-produce", status: "Completed", started: "14 Sep 2026, 08:00", duration: "11m 24s", agents: "5/5", recommendations: 4, cmsOutcome: "4 awaiting review", clarity: "Used" },
        { id: "RUN-1037", goal: "Improve citation visibility for farm-shop queries", scope: "daylesford.example/visit", status: "Completed", started: "7 Sep 2026, 08:00", duration: "9m 51s", agents: "5/5", recommendations: 3, cmsOutcome: "2 published", clarity: "Used" },
        { id: "RUN-1029", goal: "Identify product content gaps", scope: "daylesford.example/products", status: "Failed validation", started: "31 Aug 2026, 08:00", duration: "4m 12s", agents: "3/5", recommendations: 0, cmsOutcome: "No proposal", clarity: "Used" }
      ],
      cmsBundles: [
        {
          id: "CMS-218",
          title: "Organic produce page improvements",
          sourceRun: "RUN-1048",
          created: "14 Sep 2026, 08:14",
          status: "Needs review",
          connector: "Sitecore",
          environment: "Staging",
          items: [
            { id: "CMS-218-1", recommendationId: "day-rec-1", type: "FAQ", title: "Add sourcing and seasonality FAQs", target: "/products/organic-produce", risk: "Low", decision: "pending", workflow: "routine", current: "No sourcing FAQ is present in the synthetic page excerpt.", proposed: "Where does Daylesford organic produce come from?\nOur seasonal range is sourced from approved farms and growers, with availability changing through the year.", validation: "FAQ text visible on page; FAQPage markup matches rendered content." },
            { id: "CMS-218-2", recommendationId: "day-rec-2", type: "JSON-LD", title: "Expand Product and FAQPage graph", target: "/products/organic-produce", risk: "Medium", decision: "pending", workflow: "routine", current: "{\"@type\":\"Product\",\"name\":\"Organic produce\"}", proposed: "{\n  \"@context\": \"https://schema.org\",\n  \"@graph\": [\n    {\"@type\": \"Product\", \"name\": \"Organic produce\"},\n    {\"@type\": \"FAQPage\", \"mainEntity\": [{\"@type\": \"Question\", \"name\": \"Where does Daylesford organic produce come from?\", \"acceptedAnswer\": {\"@type\": \"Answer\", \"text\": \"Our seasonal range is sourced from approved farms and growers, with availability changing through the year.\"}}]}\n  ]\n}", validation: "Schema syntax, visible-content parity, and approved taxonomy (simulated checks, not a live validator)." },
            { id: "CMS-218-3", recommendationId: "day-rec-4", type: "Landing page", title: "Seasonal organic-box landing page concept", target: "New content", risk: "High", decision: "pending", workflow: "editorial", current: "No dedicated landing page.", proposed: "Draft brief: seasonal box proposition, product availability, gifting questions, delivery details, and measurement plan.", validation: "Copywriter, designer, merchandising, brand, and final publish approvals required." }
          ],
          audit: [
            "08:14 Recommendation Agent created bundle from RUN-1048",
            "08:14 Brand Context Agent bound Word v4.2 and SharePoint Published 15 Sep",
            "08:15 Awaiting item-level reviewer decisions"
          ]
        }
      ]
    },
    {
      id: "contoso",
      name: "Contoso Outdoors",
      initials: "CO",
      colour: "#0078d4",
      domain: "contoso-outdoors.example",
      scopeLabel: "Global commerce",
      activeGoal: "Improve AI visibility for trail-running products",
      health: "Needs attention",
      latestScore: 48,
      latestDelta: "+3",
      scoreTrend: {
        startDate: "2026-08-16",
        scores: [37, 37.2, 37.4, 37.5, 37.9, 38.4, 39, 39.3, 39.4, 39.6, 40.1, 40.5, 40.6, 40.8, 41.3, 41.9, 42.3, 42.4, 42.8, 43.4, 43.9, 44.1, 44.3, 44.8, 45.4, 45.9, 46.1, 46.4, 46.9, 47.5, 48]
      },
      nextRun: "18 Sep 2026, 09:00",
      lastRun: "11 Sep 2026",
      clarity: {
        status: "unavailable",
        label: "Not configured",
        projectName: null,
        lastSync: null,
        signals: []
      },
      cms: {
        type: "Shopify",
        status: "connected",
        environment: "Theme preview",
        connection: "Contoso Outdoors Global",
        lastSync: "15 Sep 2026, 09:36",
        scopes: ["Products", "Collections", "Theme metadata", "Structured data"]
      },
      staticSite: {
        type: "Azure Static Web Apps",
        label: "Static website hosting",
        status: "connected",
        environment: "Preview environment",
        connection: "contoso-geo-landing (East US)",
        endpoint: "https://contoso-geo-landing.azurestaticapps.net",
        lastSync: "15 Sep 2026, 09:44",
        scopes: ["Landing pages", "Campaign microsites"]
      },
      iq: {
        status: "connected",
        label: "Brand context ready",
        lastRefresh: "15 Sep 2026, 09:44",
        sources: [
          {
            id: "con-word",
            type: "Word",
            name: "Contoso Outdoors Product Voice.docx",
            location: "Microsoft 365 / Product Marketing",
            owner: "Global brand",
            version: "v2.8",
            updated: "9 Sep 2026",
            status: "current",
            sections: ["Product voice", "Technical claims", "Inclusive language", "Comparison guidance"]
          },
          {
            id: "con-sp",
            type: "SharePoint",
            name: "Outdoor Product Claims Library",
            location: "SharePoint / Brand / Claims",
            owner: "Legal and product",
            version: "Published 13 Sep",
            updated: "13 Sep 2026",
            status: "current",
            sections: ["Approved materials claims", "Testing evidence", "Product taxonomy", "Escalation contacts"]
          }
        ]
      },
      metrics: {
        opportunities: 5,
        awaitingReview: 2,
        activeRuns: 0,
        publishedThisMonth: 4
      },
      recommendations: [
        {
          id: "con-rec-1",
          priority: "High",
          rank: 1,
          category: "Product description",
          title: "Explain terrain fit and cushioning in the product description",
          target: "/products/trail-runner-pro",
          impact: "High",
          effort: "Low",
          confidence: "Medium",
          owner: "Product merchandising",
          status: "Ready for review",
          brandAlignment: "Aligned",
          brandSource: "Contoso Outdoors Product Voice.docx, Technical claims",
          summary: "Add evidence-backed language that helps buyers and answer engines understand the intended use.",
          proposedChange: "Add concise terrain, cushioning, and fit guidance using only approved product specifications.",
          verification: "Product-owner check, claims-library validation, and next scheduled WebIQ evaluation.",
          evidence: {
            webiq: "Returned comparison pages answer terrain and cushioning questions directly in product copy.",
            clarity: "Clarity unavailable for this project; confidence excludes on-site behavior.",
            iq: "Use tested specifications only and avoid claims such as 'best' or 'injury preventing'."
          }
        },
        {
          id: "con-rec-2",
          priority: "Medium",
          rank: 2,
          category: "FAQ",
          title: "Add fit and care FAQs",
          target: "/products/trail-runner-pro",
          impact: "Medium",
          effort: "Low",
          confidence: "Low",
          owner: "Customer experience",
          status: "Proposed",
          brandAlignment: "Aligned",
          brandSource: "Outdoor Product Claims Library, Product taxonomy",
          summary: "Answer sizing, waterproofing, and care questions with approved terminology.",
          proposedChange: "Draft three visible FAQs and matching FAQPage structured data.",
          verification: "Customer-support review, schema validation, and evidence re-run.",
          evidence: {
            webiq: "Fit and care questions appear across four grounded discovery-query result sets.",
            clarity: "No project analytics source is configured.",
            iq: "Use 'water-resistant' for this product; 'waterproof' is not approved."
          }
        }
      ],
      runs: [
        { id: "RUN-2084", goal: "Improve AI visibility for trail-running products", scope: "contoso-outdoors.example/products/trail-runner-pro", status: "Completed", started: "11 Sep 2026, 09:00", duration: "8m 33s", agents: "4/5", recommendations: 2, cmsOutcome: "2 awaiting review", clarity: "Skipped" },
        { id: "RUN-2076", goal: "Improve collection-page grounding", scope: "contoso-outdoors.example/collections/trail", status: "Completed", started: "4 Sep 2026, 09:00", duration: "7m 49s", agents: "4/5", recommendations: 2, cmsOutcome: "1 published", clarity: "Skipped" }
      ],
      cmsBundles: [
        {
          id: "CMS-391",
          title: "Trail Runner Pro content update",
          sourceRun: "RUN-2084",
          created: "11 Sep 2026, 09:11",
          status: "Needs review",
          connector: "Shopify",
          environment: "Theme preview",
          items: [
            { id: "CMS-391-1", recommendationId: "con-rec-1", type: "Product description", title: "Add terrain and cushioning guidance", target: "/products/trail-runner-pro", risk: "Low", decision: "pending", workflow: "routine", current: "A responsive trail shoe for outdoor adventures.", proposed: "Designed for mixed trail terrain, with tested midsole cushioning and a secure fit for everyday trail sessions.", validation: "Approved specifications only; no unqualified performance or health claims." },
            { id: "CMS-391-2", recommendationId: "con-rec-2", type: "FAQ", title: "Add fit and care guidance", target: "/products/trail-runner-pro", risk: "Low", decision: "pending", workflow: "routine", current: "No fit or care FAQs.", proposed: "How should the Trail Runner Pro fit?\nChoose your usual Contoso size for a secure trail fit. Review the size guide before ordering.", validation: "Terminology matches product taxonomy; FAQ markup matches visible text." }
          ],
          audit: [
            "09:11 Recommendation Agent created bundle from RUN-2084",
            "09:11 Brand Context Agent bound Word v2.8 and SharePoint Published 13 Sep",
            "09:12 Awaiting item-level reviewer decisions"
          ]
        }
      ]
    }
  ],
  goals: [
    "Increase AI referral traffic",
    "Improve citation visibility",
    "Identify content gaps",
    "Strengthen product-page grounding"
  ],
  agentBlueprint: [
    { id: "clarity", name: "Clarity analytics agent", description: "Reads configured project analytics and behavior signals.", duration: "1m 18s", durationMs: 78000 },
    { id: "webiq", name: "WebIQ evidence agent", description: "Retrieves bounded external grounding and citation evidence.", duration: "3m 42s", durationMs: 222000 },
    { id: "evaluation", name: "GEO evaluation agent", description: "Assesses discoverability, evidence coverage, and content gaps.", duration: "2m 09s", durationMs: 129000 },
    { id: "brand", name: "Brand Context agent", description: "References approved Word and SharePoint guidance through Microsoft IQ.", duration: "52s", durationMs: 52000 },
    { id: "recommendation", name: "Recommendation agent", description: "Prioritizes reviewable actions with evidence and limitations.", duration: "1m 36s", durationMs: 96000 }
  ]
};
