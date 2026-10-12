# 🩸 BloodBond — Real-Time Blood Donor Matching Platform

**Every second counts. Every donation can save a life.**

BloodBond is a blood donor matching platform designed to connect people who need blood with compatible donors and hospitals. It aims to reduce delays in finding blood donors through smart matching based on blood group and distance, phone OTP authentication, and blood request status updates.

🌐 **Live Demo:** [blood-bond-five.vercel.app](https://blood-bond-five.vercel.app/)

## ✨ Key Features

- 🩸 **Smart Blood Matching** — Prioritizes compatible donors based on blood group and distance.
- 👤 **Donor Registration** — Provides a workflow for donors to register and participate in blood donation.
- 🏥 **Blood Request Management** — Helps users and hospitals coordinate blood requirements.
- 📱 **OTP-Based Authentication** — Phone number verification workflow for user authentication.
- 🔔 **SMS Alerts** — Designed to notify donors about blood requests.
- 📍 **Location-Aware Matching** — Uses distance to help prioritize nearby donors without displaying their exact location publicly.
- 🔄 **Request Status Updates** — Helps users track blood requests and donor responses.
- 🛡️ **Donation Safety** — Includes a 56-day donation safety lock concept.
- 📱 **Responsive Interface** — A simple, accessible interface for interacting with the platform.
- 🌙 **Light Mode** — Includes a theme-switching option.

## 🎯 Problem Statement

Finding compatible blood donors during emergencies can be difficult and time-consuming. Delays in communication and a lack of organized donor information can make the process even harder.

BloodBond aims to address these challenges by providing a centralized platform where blood requests and compatible donors can be connected more efficiently.

## 💡 Our Solution

BloodBond brings blood requesters, donors, and hospitals into a single workflow. By combining blood-group compatibility, distance-based prioritization, phone verification, and request status tracking, the platform aims to make the blood donation process faster and more organized.

## 🛠️ Tech Stack

### Frontend
- HTML5
- CSS3
- JavaScript

### Backend
- Python
- FastAPI
- REST API

### Database
- PostgreSQL (Supabase-hosted production database)
- SQLite for local development fallback

### Deployment
- **Frontend:** Vercel
- **Backend:** Render
- **Database:** Supabase


## 🧪 Demo Login

The deployed login interface provides a demo authentication flow.

- Enter a 10-digit phone number.
- Use the demo OTP `123456` when demo mode is enabled.
- Complete the verification flow to continue.

**Note:** The demo OTP is for testing only. It should not be treated as secure production authentication.

## 🔒 Security and Privacy

BloodBond is designed with user privacy and responsible blood donation in mind.

- Phone-based verification workflow.
- Protection of donors' exact locations.
- Donation interval safety checks.
- Secure handling of authentication and database credentials.
- CORS configuration for authorized frontend origins.

Production use requires properly configured authentication, server-side authorization, privacy controls, and verified medical eligibility procedures.

## 🌱 Future Enhancements

- Real-time donor notifications through an SMS provider.
- Improved donor availability and response tracking.
- Hospital and blood bank dashboards.
- More advanced location-based donor matching.
- Blood inventory availability and management.
- Emergency request prioritization.
- Multilingual support.
- Automated testing, monitoring, and performance improvements.

## 🤝 Contributing

Contributions, ideas, and suggestions are welcome.

1. Fork the repository.
2. Create a feature branch.
3. Implement your changes.
4. Test your changes.
5. Submit a pull request.

```bash
git checkout -b feature/your-feature
git add .
git commit -m "Add: your feature"
git push origin feature/your-feature
```

## ⚠️ Disclaimer

BloodBond is a technology project intended to support blood donor coordination. It does not replace professional medical advice, hospital procedures, blood bank screening, or clinical compatibility verification. Blood availability and donor eligibility must be confirmed by authorized medical professionals.

## ❤️ Our Mission

**To make finding compatible blood donors faster, simpler, and more accessible when every second matters.**

Built with ❤️ for a meaningful cause.
