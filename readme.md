# KISEKAE

Kisekae is an online fashion store, that lets users try on clothing apparels using augmented reality through their phone's camera, allowing them to make confident purchases.

## Features

### 1. Authentication

- Users can sign up using their email or phone number
- Users can login with password or OTP sent to their mobile or email
- Uses JWT for Authentication

### 2. Augmented Reality Try-on

- Users can point their phone camera at a subject to see how a clothing item looks on them in real time
- Can also upload the picture of the subject from their gallery
- The outfit adjusts naturally as the subject moves, turns, or changes posture
- Users can capture a photo or short clip of themselves in the outfit to save or compare later

### 3. Mix & Match

- In the AR try-on user can combine multiple products and try them together to see how they fit
- User can also cross [match across categories]
- Users can switch between different sizes or colors of the same item while trying it on
- Save the outfits as combos
- Users can share saved combos as a URL, which can be accessed by others to also try on

### 4. Product Catalog

- Users can browse clothing collection organised by categories
- Users can search with filters for size, color, price range, and clothing type
- Product card with thumbnail image, colors, and price
- Users get their own curated feed for recommended products

### 5. Product Pages

- Contains detailed view of the product
- Includes images, description, price, and available sizes/colors
- Also checkout reviews and ratings of the product
- Size chart to help users pick the correct fit
- Direct access to launch the AR try-on from the page

### 6. Wishlist

- Users can save items they're interested in to a wishlist for later
- Wishlisted items can be bulk moved directly to cart

### 7. User Profile

- Users can manage personal details, saved addresses, and payment methods
- View saved try-on snapshots linked to their profile
- View their orders and check their status
- Change their settings

### 8. Cart & Checkout

- Users can add items to their cart, edit quantities, and select size/color before purchase
- Secure payment processing supporting multiple payment methods (cards, UPI, wallets)
- View order summary
- View estimated delivery time

### 9. Notification System

- Users receive order confirmation, shipping, and delivery updates via email, SMS, and in-app push notifications
- Users can customize which channels (email/SMS/push) they want to receive notifications on, through user profile settings
- Promotional and sales notifications

## Monitoring & Observability

The application is instrumented with Prometheus and comes with Grafana dashboards pre-configured.

### Services & Endpoints

| Service | URL | Description | Credentials (Default) |
| --- | --- | --- | --- |
| **Django Metrics** | `http://localhost:8000/metrics` | Prometheus metrics endpoint exported by `django-prometheus` | N/A |
| **Prometheus** | `http://localhost:9090` | Prometheus UI, query browser, and targets dashboard | None |
| **Grafana** | `http://localhost:3000` | Grafana metrics dashboard & visualizations | User: `admin`, Password: `admin` |

### Running the Monitoring Stack

Start all services including Prometheus and Grafana using Docker Compose:

```bash
docker compose up -d
```

### Pre-provisioned Dashboards

Grafana automatically provisions the Prometheus datasource and imports the **Django Application Overview** dashboard (`django-overview`), providing out-of-the-box visibility into:
- Total request throughput and response rates (2xx, 4xx, 5xx)
- P50, P95, and P99 latency percentiles
- Requests per view/endpoint and HTTP method
- Database execution rates, query durations, and error rates
- Python runtime statistics (garbage collection, memory)