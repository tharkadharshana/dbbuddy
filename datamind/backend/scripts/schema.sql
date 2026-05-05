-- Comprehensive POS Schema for DataMind AI
DROP DATABASE IF EXISTS datamind_db;
CREATE DATABASE datamind_db;
USE datamind_db;

-- 1. Locations Table (Added for realism)
CREATE TABLE IF NOT EXISTS locations (
    location_id INT AUTO_INCREMENT PRIMARY KEY,
    location_name VARCHAR(100) NOT NULL,
    address TEXT,
    licenceKey VARCHAR(100) UNIQUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
);

-- 2. Customers Table
CREATE TABLE IF NOT EXISTS customers (
    customerId VARCHAR(50) PRIMARY KEY,
    name VARCHAR(100),
    email VARCHAR(100),
    loyaltyPoints INT DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
);

-- 3. Products Table
CREATE TABLE IF NOT EXISTS products (
    itemCode VARCHAR(50) PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    category VARCHAR(100),
    basePrice DECIMAL(12, 2),
    baseCost DECIMAL(12, 2),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
);

-- 4. Employees Table (Added for realism)
CREATE TABLE IF NOT EXISTS employees (
    empId VARCHAR(50) PRIMARY KEY,
    cashierName VARCHAR(100) NOT NULL,
    location_id INT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    FOREIGN KEY (location_id) REFERENCES locations(location_id)
);

-- 5. Invoices (Header)
CREATE TABLE IF NOT EXISTS invoices (
    invoiceNumber VARCHAR(50) PRIMARY KEY,
    mainInvoiceNumber VARCHAR(50),
    invoiceCentralizeId VARCHAR(50),
    uniqueId VARCHAR(100) UNIQUE,
    reference VARCHAR(100),
    kotNumber VARCHAR(50),
    orderNumber VARCHAR(50),
    licenceKey VARCHAR(100),
    
    -- Customer Info
    customerId VARCHAR(50),
    customerCash DECIMAL(12, 2),
    customerBalance DECIMAL(12, 2),
    invoiceCustomerSignatures TEXT, -- JSON or URL
    
    -- Financial Data (Header)
    invoiceTotal DECIMAL(12, 2), -- invoice_amount
    totalDiscount DECIMAL(12, 2), -- finalTotalDiscount
    chargeTotalTax DECIMAL(12, 2), -- productTax
    chargeTotalCharge DECIMAL(12, 2),
    creditAmount DECIMAL(12, 2),
    creditComplete BOOLEAN DEFAULT FALSE,
    creditDays INT DEFAULT 0,
    
    -- Payment Information
    payMethod VARCHAR(50),
    chequeNo VARCHAR(100),
    cardNo VARCHAR(20),
    payment TEXT, -- JSON object/string
    splitPayment TEXT, -- JSON
    loyaltyPoints INT, -- points earned/used in this invoice
    
    -- Temporal Data
    invoiceDate DATE,
    invoiceTime TIME,
    customizeTime TIME,
    startDateTime DATETIME,
    startTime TIME,
    endTime TIME,
    
    -- Operational Details
    cashierName VARCHAR(100),
    empId VARCHAR(50),
    tableCode VARCHAR(50),
    orderType VARCHAR(50),
    pickupDetails TEXT,
    billNote TEXT,
    channel VARCHAR(50),
    location_id INT,
    
    FOREIGN KEY (customerId) REFERENCES customers(customerId),
    FOREIGN KEY (empId) REFERENCES employees(empId),
    FOREIGN KEY (location_id) REFERENCES locations(location_id)
);

-- 6. Invoice Line Items
CREATE TABLE IF NOT EXISTS invoice_items (
    id INT AUTO_INCREMENT PRIMARY KEY,
    invoiceNumber VARCHAR(50),
    itemCode VARCHAR(50),
    qty DECIMAL(12, 3),
    itemPrice DECIMAL(12, 2),
    itemCost DECIMAL(12, 2),
    discount DECIMAL(12, 2),
    taxValue DECIMAL(12, 2),
    itemRemark TEXT,
    itemsDataList TEXT, -- JSON of products
    invoiceLineDiscountList TEXT, -- JSON
    invoiceItemAddonList TEXT, -- JSON (itemAddons)
    
    FOREIGN KEY (invoiceNumber) REFERENCES invoices(invoiceNumber),
    FOREIGN KEY (itemCode) REFERENCES products(itemCode)
);

-- 7. Inventory & Audit Logs (For Prediction)
CREATE TABLE IF NOT EXISTS inventory_logs (
    log_id INT AUTO_INCREMENT PRIMARY KEY,
    itemCode VARCHAR(50),
    location_id INT,
    change_qty DECIMAL(12, 3),
    reason VARCHAR(100), -- SALE, RESTOCK, DAMAGE
    log_date DATE,
    log_time TIME,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (itemCode) REFERENCES products(itemCode),
    FOREIGN KEY (location_id) REFERENCES locations(location_id)
);
