-- ============================================
-- VERIFICATION AND TESTING SCRIPT
-- Run this to verify both schemas are working
-- ============================================

USE mysite;

-- ============================================
-- 1. VERIFY ALL TABLES EXIST
-- ============================================

SELECT '📊 TABLE VERIFICATION' as '';
SELECT 
    table_name,
    table_type,
    engine,
    table_rows
FROM information_schema.tables 
WHERE table_schema = 'mysite' 
    AND table_type = 'BASE TABLE'
ORDER BY table_name;

-- ============================================
-- 2. VERIFY ALL VIEWS EXIST
-- ============================================

SELECT '👁️ VIEW VERIFICATION' as '';
SELECT 
    table_name,
    table_type
FROM information_schema.tables 
WHERE table_schema = 'mysite' 
    AND table_type = 'VIEW'
ORDER BY table_name;

-- ============================================
-- 3. VERIFY ALL FUNCTIONS
-- ============================================

SELECT '⚙️ FUNCTION VERIFICATION' as '';
SELECT 
    routine_name,
    routine_type,
    data_type
FROM information_schema.routines 
WHERE routine_schema = 'mysite' 
    AND routine_type = 'FUNCTION'
ORDER BY routine_name;

-- ============================================
-- 4. VERIFY ALL PROCEDURES
-- ============================================

SELECT '📝 PROCEDURE VERIFICATION' as '';
SELECT 
    routine_name,
    routine_type
FROM information_schema.routines 
WHERE routine_schema = 'mysite' 
    AND routine_type = 'PROCEDURE'
ORDER BY routine_name;

-- ============================================
-- 5. VERIFY ALL TRIGGERS
-- ============================================

SELECT '🔔 TRIGGER VERIFICATION' as '';
SELECT 
    trigger_name,
    event_manipulation,
    event_object_table
FROM information_schema.triggers 
WHERE trigger_schema = 'mysite'
ORDER BY trigger_name;

-- ============================================
-- 6. TEST COUNTDOWN CARDS DATA
-- ============================================

SELECT '📅 COUNTDOWN CARDS' as '';
SELECT 
    title,
    description,
    start_time,
    end_time,
    status,
    is_published
FROM countdown_cards;

-- ============================================
-- 7. TEST IMAGE POSTS DATA
-- ============================================

SELECT '🖼️ IMAGE POSTS' as '';
SELECT 
    title,
    category,
    cloudinary_url,
    display_order,
    status
FROM image_posts;

-- ============================================
-- 8. TEST ACTIVE VIEWS
-- ============================================

SELECT '🔴 ACTIVE COUNTDOWN CARDS VIEW' as '';
SELECT * FROM active_countdown_cards;

SELECT '🟢 ACTIVE IMAGE POSTS VIEW' as '';
SELECT * FROM active_image_posts;

-- ============================================
-- 9. TEST USER MANAGEMENT TABLES
-- ============================================

SELECT '👥 USERS' as '';
SELECT 
    full_name,
    registration_number,
    email,
    status,
    role,
    is_admin
FROM users;

SELECT '📋 FRAMEWORK FIELDS' as '';
SELECT 
    field_name,
    field_type,
    is_required,
    display_order
FROM user_framework_fields
ORDER BY display_order;

SELECT '👥 USER GROUPS' as '';
SELECT 
    group_name,
    group_code,
    max_members,
    current_members,
    is_active,
    is_flagged
FROM user_groups;

SELECT '⚙️ SYSTEM SETTINGS' as '';
SELECT 
    setting_key,
    setting_value,
    setting_type,
    description
FROM system_settings;

-- ============================================
-- 10. TEST USER MANAGEMENT VIEWS
-- ============================================

SELECT '👥 ACTIVE USERS VIEW' as '';
SELECT * FROM v_active_users;

SELECT '🚩 FLAGGED USERS VIEW' as '';
SELECT * FROM v_flagged_users;

SELECT '📊 GROUP SUMMARY VIEW' as '';
SELECT * FROM v_group_summary;

-- ============================================
-- 11. TEST STORED PROCEDURES
-- ============================================

-- Test adding a test user (uncomment to run)
/*
INSERT INTO users (full_name, registration_number, email, phone, status, role)
VALUES ('Test User', 'TEST001', 'test@example.com', '+1234567890', 'active', 'member');

-- Test flagging a user
CALL flag_user(2, 'Test flag reason', 'Test case details', 'medium', 1);

-- Test unflagging
CALL unflag_user(2, 1);

-- Test adding to group
CALL add_user_to_group(2, 1, FALSE);

-- Test moving between groups
CALL move_user_to_group(2, 1, 2);
*/

-- ============================================
-- 12. TEST FUNCTIONS
-- ============================================

-- Test expire_old_image_posts function
SELECT '⏰ EXPIRE OLD IMAGE POSTS' as '';
SELECT expire_old_image_posts() as expired_count;

-- Test get_user_registration_data (uncomment after adding registration data)
-- SELECT get_user_registration_data(1) as user_data;

-- ============================================
-- 13. REAL-TIME FEATURES TEST
-- ============================================

-- Test trigger: Add a user to a group and check member count updates
SELECT '🔔 TESTING GROUP MEMBERSHIP TRIGGERS' as '';

-- Create a test group member
INSERT INTO user_group_members (user_id, group_id, is_leader)
VALUES (1, 1, FALSE)
ON DUPLICATE KEY UPDATE status = 'active';

-- Check updated group member count
SELECT 
    group_name,
    current_members
FROM user_groups
WHERE id = 1;

-- Test session creation
INSERT INTO user_sessions (session_id, user_id, ip_address, user_agent)
VALUES (UUID(), 1, '127.0.0.1', 'Test Browser/1.0');

SELECT 
    session_id,
    user_id,
    login_time,
    last_activity,
    is_active
FROM user_sessions
WHERE user_id = 1
ORDER BY login_time DESC
LIMIT 1;

-- Test activity logging
INSERT INTO user_activity_logs (user_id, action, entity_type, entity_id, ip_address)
VALUES (1, 'test_action', 'user', '1', '127.0.0.1');

SELECT 
    action,
    entity_type,
    created_at,
    ip_address
FROM user_activity_logs
WHERE user_id = 1
ORDER BY created_at DESC
LIMIT 5;

-- ============================================
-- 14. CHECK FOREIGN KEY RELATIONSHIPS
-- ============================================

SELECT '🔗 FOREIGN KEY VERIFICATION' as '';
SELECT 
    TABLE_NAME,
    COLUMN_NAME,
    CONSTRAINT_NAME,
    REFERENCED_TABLE_NAME,
    REFERENCED_COLUMN_NAME
FROM information_schema.KEY_COLUMN_USAGE
WHERE TABLE_SCHEMA = 'mysite'
    AND REFERENCED_TABLE_NAME IS NOT NULL
ORDER BY TABLE_NAME, COLUMN_NAME;

-- ============================================
-- 15. CHECK INDEXES
-- ============================================

SELECT '📇 INDEX VERIFICATION' as '';
SELECT 
    TABLE_NAME,
    INDEX_NAME,
    COLUMN_NAME,
    NON_UNIQUE
FROM information_schema.STATISTICS
WHERE TABLE_SCHEMA = 'mysite'
    AND INDEX_NAME != 'PRIMARY'
ORDER BY TABLE_NAME, INDEX_NAME;

-- ============================================
-- 16. SAMPLE DATA FOR USER REGISTRATION TEST
-- ============================================

-- Add sample user registration data (uncomment to run)
/*
INSERT INTO user_registrations (user_id, field_id, field_value)
SELECT 
    1, 
    id,
    CASE 
        WHEN field_name = 'Full Name' THEN 'System Administrator'
        WHEN field_name = 'Registration Number' THEN 'ADMIN001'
        WHEN field_name = 'Phone Number' THEN '+1234567890'
        WHEN field_name = 'Email Address' THEN 'admin@ring0.com'
        ELSE 'N/A'
    END
FROM user_framework_fields;

-- Verify registration data
SELECT 
    u.full_name,
    u.registration_number,
    f.field_name,
    r.field_value
FROM users u
JOIN user_registrations r ON u.id = r.user_id
JOIN user_framework_fields f ON r.field_id = f.id
WHERE u.id = 1;
*/

-- ============================================
-- 17. CLEANUP TEST DATA (Optional)
-- ============================================

-- Uncomment to clean up test data
/*
DELETE FROM user_activity_logs WHERE action = 'test_action';
DELETE FROM user_sessions WHERE user_agent = 'Test Browser/1.0';
DELETE FROM user_group_members WHERE user_id = 1 AND group_id = 1;
*/

-- ============================================
-- 18. FINAL SUMMARY
-- ============================================

SELECT '✅ SCHEMA VERIFICATION COMPLETE!' as 'Status';
SELECT 
    COUNT(DISTINCT CASE WHEN table_type = 'BASE TABLE' THEN table_name END) as total_tables,
    COUNT(DISTINCT CASE WHEN table_type = 'VIEW' THEN table_name END) as total_views,
    COUNT(DISTINCT routine_name) as total_functions_procedures,
    COUNT(DISTINCT trigger_name) as total_triggers
FROM information_schema.tables t
LEFT JOIN information_schema.routines r ON r.routine_schema = t.table_schema
LEFT JOIN information_schema.triggers tr ON tr.trigger_schema = t.table_schema
WHERE t.table_schema = 'mysite'
    AND t.table_type IN ('BASE TABLE', 'VIEW')
UNION ALL
SELECT 'Total Tables' as '',
       COUNT(*) as '',
       '' as '',
       '' as ''
FROM information_schema.tables 
WHERE table_schema = 'mysite' AND table_type = 'BASE TABLE'
UNION ALL
SELECT 'Total Views' as '',
       COUNT(*) as '',
       '' as '',
       '' as ''
FROM information_schema.tables 
WHERE table_schema = 'mysite' AND table_type = 'VIEW'
UNION ALL
SELECT 'Total Functions' as '',
       COUNT(*) as '',
       '' as '',
       '' as ''
FROM information_schema.routines 
WHERE routine_schema = 'mysite' AND routine_type = 'FUNCTION'
UNION ALL
SELECT 'Total Procedures' as '',
       COUNT(*) as '',
       '' as '',
       '' as ''
FROM information_schema.routines 
WHERE routine_schema = 'mysite' AND routine_type = 'PROCEDURE'
UNION ALL
SELECT 'Total Triggers' as '',
       COUNT(*) as '',
       '' as '',
       '' as ''
FROM information_schema.triggers 
WHERE trigger_schema = 'mysite';






-- ============================================
-- LOST & FOUND PROPERTIES SYSTEM SCHEMA
-- Version 1.0 - With Real-time Features
-- Date: 2026-03-26
-- ============================================

-- Select your database
USE mysite;

-- ============================================
-- CLEAN DROP - Remove existing property tables if any
-- ============================================

DROP TABLE IF EXISTS property_claims;
DROP TABLE IF EXISTS property_notifications;
DROP TABLE IF EXISTS property_activity_logs;
DROP TABLE IF EXISTS properties;

-- ============================================
-- 1. PROPERTIES TABLE - Core lost & found items
-- ============================================

CREATE TABLE properties (
    id INT AUTO_INCREMENT PRIMARY KEY,
    uuid CHAR(36) DEFAULT (UUID()),
    category ENUM('id', 'other') NOT NULL DEFAULT 'other',
    property_type VARCHAR(100) NOT NULL,
    image_url TEXT NOT NULL,
    cloudinary_public_id VARCHAR(255),
    cloudinary_status ENUM('uploaded', 'local', 'failed') DEFAULT 'local',
    owner_name VARCHAR(255),
    registration_number VARCHAR(100),
    description TEXT,
    location_found VARCHAR(255) NOT NULL,
    found_date DATE NOT NULL,
    found_time TIME,
    status ENUM('unclaimed', 'claimed', 'resolved', 'archived') DEFAULT 'unclaimed',
    finder_name VARCHAR(255),
    finder_contact VARCHAR(255),
    finder_email VARCHAR(255),
    finder_phone VARCHAR(50),
    added_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    is_active BOOLEAN DEFAULT TRUE,
    notes TEXT,
    INDEX idx_category (category),
    INDEX idx_status (status),
    INDEX idx_found_date (found_date),
    INDEX idx_added_date (added_date),
    INDEX idx_location (location_found),
    INDEX idx_property_type (property_type),
    INDEX idx_registration (registration_number),
    INDEX idx_owner_name (owner_name),
    FULLTEXT INDEX idx_description (description)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ============================================
-- 2. PROPERTY CLAIMS TABLE - Claim management
-- ============================================

CREATE TABLE property_claims (
    id INT AUTO_INCREMENT PRIMARY KEY,
    property_id INT NOT NULL,
    claimant_name VARCHAR(255) NOT NULL,
    claimant_email VARCHAR(255),
    claimant_phone VARCHAR(50),
    claimant_registration VARCHAR(100),
    claimant_message TEXT,
    proof_details TEXT,
    claim_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    status ENUM('pending', 'approved', 'rejected', 'completed') DEFAULT 'pending',
    admin_notes TEXT,
    processed_by INT,
    processed_date TIMESTAMP NULL,
    resolved_date TIMESTAMP NULL,
    FOREIGN KEY (property_id) REFERENCES properties(id) ON DELETE CASCADE,
    INDEX idx_property (property_id),
    INDEX idx_status (status),
    INDEX idx_claim_date (claim_date),
    INDEX idx_claimant (claimant_name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ============================================
-- 3. PROPERTY NOTIFICATIONS TABLE - Real-time alerts
-- ============================================

CREATE TABLE property_notifications (
    id INT AUTO_INCREMENT PRIMARY KEY,
    property_id INT,
    notification_type ENUM('new_property', 'claim_submitted', 'claim_approved', 'claim_rejected', 'property_claimed') NOT NULL,
    title VARCHAR(255) NOT NULL,
    message TEXT NOT NULL,
    target_audience ENUM('all', 'admin', 'finder', 'claimant') DEFAULT 'all',
    target_email VARCHAR(255),
    target_phone VARCHAR(50),
    is_read BOOLEAN DEFAULT FALSE,
    read_at TIMESTAMP NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMP NULL,
    FOREIGN KEY (property_id) REFERENCES properties(id) ON DELETE SET NULL,
    INDEX idx_type (notification_type),
    INDEX idx_read (is_read),
    INDEX idx_created (created_at),
    INDEX idx_expires (expires_at),
    INDEX idx_target (target_audience, target_email)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ============================================
-- 4. PROPERTY ACTIVITY LOGS - Audit trail
-- ============================================

CREATE TABLE property_activity_logs (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    property_id INT,
    action VARCHAR(100) NOT NULL,
    action_by ENUM('admin', 'finder', 'claimant', 'system') NOT NULL,
    user_id INT,
    user_name VARCHAR(255),
    user_email VARCHAR(255),
    old_status VARCHAR(50),
    new_status VARCHAR(50),
    changes TEXT,
    ip_address VARCHAR(45),
    user_agent TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (property_id) REFERENCES properties(id) ON DELETE SET NULL,
    INDEX idx_property (property_id),
    INDEX idx_action (action),
    INDEX idx_created (created_at),
    INDEX idx_user (user_id, user_email)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ============================================
-- 5. ADMIN SESSIONS (Optional - for admin tracking)
-- ============================================

CREATE TABLE property_admin_sessions (
    id INT AUTO_INCREMENT PRIMARY KEY,
    session_id VARCHAR(255) UNIQUE NOT NULL,
    admin_name VARCHAR(255),
    admin_email VARCHAR(255),
    login_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_activity TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    ip_address VARCHAR(45),
    user_agent TEXT,
    is_active BOOLEAN DEFAULT TRUE,
    INDEX idx_session (session_id),
    INDEX idx_last_activity (last_activity)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ============================================
-- 6. TRIGGERS FOR REAL-TIME UPDATES
-- ============================================

DELIMITER //

-- Trigger to create notification when new property is added
CREATE TRIGGER trg_property_insert
AFTER INSERT ON properties
FOR EACH ROW
BEGIN
    -- Create notification for admin
    INSERT INTO property_notifications (property_id, notification_type, title, message, target_audience)
    VALUES (
        NEW.id, 
        'new_property',
        CONCAT('New ', UPPER(NEW.category), ' Property Added'),
        CONCAT('A new ', NEW.property_type, ' was found at ', NEW.location_found, ' on ', DATE_FORMAT(NEW.found_date, '%Y-%m-%d')),
        'admin'
    );
    
    -- Log activity
    INSERT INTO property_activity_logs (property_id, action, action_by, user_name, new_status)
    VALUES (NEW.id, 'property_added', 'finder', NEW.finder_name, NEW.status);
END //

-- Trigger when property status changes
CREATE TRIGGER trg_property_status_update
AFTER UPDATE ON properties
FOR EACH ROW
BEGIN
    IF OLD.status != NEW.status THEN
        -- Log status change
        INSERT INTO property_activity_logs (property_id, action, action_by, old_status, new_status)
        VALUES (NEW.id, 'status_change', 'system', OLD.status, NEW.status);
        
        -- Create notification based on new status
        IF NEW.status = 'claimed' THEN
            INSERT INTO property_notifications (property_id, notification_type, title, message, target_audience)
            VALUES (
                NEW.id,
                'property_claimed',
                'Property Claimed',
                CONCAT('Property "', NEW.property_type, '" has been claimed'),
                'admin'
            );
        END IF;
    END IF;
END //

-- Trigger for new claims
CREATE TRIGGER trg_claim_insert
AFTER INSERT ON property_claims
FOR EACH ROW
BEGIN
    -- Notify admin about new claim
    INSERT INTO property_notifications (property_id, notification_type, title, message, target_audience)
    VALUES (
        NEW.property_id,
        'claim_submitted',
        'New Claim Submitted',
        CONCAT(NEW.claimant_name, ' has submitted a claim for property #', NEW.property_id),
        'admin'
    );
    
    -- Log claim activity
    INSERT INTO property_activity_logs (property_id, action, action_by, user_name, user_email, changes)
    VALUES (
        NEW.property_id,
        'claim_submitted',
        'claimant',
        NEW.claimant_name,
        NEW.claimant_email,
        CONCAT('Claim details: ', NEW.claimant_message)
    );
END //

-- Trigger for claim status updates
CREATE TRIGGER trg_claim_status_update
AFTER UPDATE ON property_claims
FOR EACH ROW
BEGIN
    IF OLD.status != NEW.status THEN
        -- Log claim status change
        INSERT INTO property_activity_logs (property_id, action, action_by, old_status, new_status)
        VALUES (NEW.property_id, 'claim_status_change', 'system', OLD.status, NEW.status);
        
        -- Notify claimant about status change
        IF NEW.status IN ('approved', 'rejected') THEN
            INSERT INTO property_notifications (
                property_id, 
                notification_type, 
                title, 
                message, 
                target_audience,
                target_email
            )
            VALUES (
                NEW.property_id,
                CONCAT('claim_', NEW.status),
                CONCAT('Claim ', UPPER(NEW.status)),
                CONCAT('Your claim for property #', NEW.property_id, ' has been ', NEW.status),
                'claimant',
                NEW.claimant_email
            );
        END IF;
    END IF;
END //

DELIMITER ;

-- ============================================
-- 7. VIEWS FOR COMMON QUERIES
-- ============================================

-- Active properties view (unclaimed only)
CREATE OR REPLACE VIEW v_active_properties AS
SELECT 
    id, uuid, category, property_type, image_url, cloudinary_status,
    owner_name, registration_number, description, location_found,
    found_date, found_time, status, added_date,
    CASE 
        WHEN DATEDIFF(NOW(), added_date) = 0 THEN 'Today'
        WHEN DATEDIFF(NOW(), added_date) = 1 THEN 'Yesterday'
        ELSE CONCAT(DATEDIFF(NOW(), added_date), ' days ago')
    END as time_ago,
    TIMESTAMPDIFF(DAY, added_date, NOW()) as days_since_added
FROM properties
WHERE status = 'unclaimed' AND is_active = TRUE
ORDER BY added_date DESC;

-- All properties with claim info view
CREATE OR REPLACE VIEW v_properties_with_claims AS
SELECT 
    p.*,
    COUNT(c.id) as claim_count,
    MAX(CASE WHEN c.status = 'pending' THEN c.id END) as pending_claim_id,
    MAX(CASE WHEN c.status = 'approved' THEN c.id END) as approved_claim_id,
    MAX(CASE WHEN c.status = 'completed' THEN c.id END) as completed_claim_id,
    GROUP_CONCAT(DISTINCT c.claimant_name ORDER BY c.claim_date DESC SEPARATOR ', ') as claimants
FROM properties p
LEFT JOIN property_claims c ON p.id = c.property_id
GROUP BY p.id;

-- Recent properties view (last 7 days)
CREATE OR REPLACE VIEW v_recent_properties AS
SELECT 
    id, uuid, category, property_type, image_url,
    owner_name, registration_number, description, location_found,
    found_date, added_date, status,
    DATE_FORMAT(added_date, '%Y-%m-%d %H:%i') as formatted_date
FROM properties
WHERE added_date >= DATE_SUB(NOW(), INTERVAL 7 DAY)
    AND is_active = TRUE
ORDER BY added_date DESC;

-- Claim statistics view
CREATE OR REPLACE VIEW v_claim_statistics AS
SELECT 
    p.id as property_id,
    p.property_type,
    p.category,
    p.status as property_status,
    COUNT(c.id) as total_claims,
    SUM(CASE WHEN c.status = 'pending' THEN 1 ELSE 0 END) as pending_claims,
    SUM(CASE WHEN c.status = 'approved' THEN 1 ELSE 0 END) as approved_claims,
    SUM(CASE WHEN c.status = 'rejected' THEN 1 ELSE 0 END) as rejected_claims,
    MAX(c.claim_date) as last_claim_date,
    MAX(CASE WHEN c.status = 'pending' THEN c.claimant_name END) as latest_claimant
FROM properties p
LEFT JOIN property_claims c ON p.id = c.property_id
GROUP BY p.id, p.property_type, p.category, p.status;

-- Dashboard stats view
CREATE OR REPLACE VIEW v_dashboard_stats AS
SELECT 
    COUNT(*) as total_properties,
    SUM(CASE WHEN category = 'id' THEN 1 ELSE 0 END) as total_ids,
    SUM(CASE WHEN category = 'other' THEN 1 ELSE 0 END) as total_other,
    SUM(CASE WHEN status = 'unclaimed' THEN 1 ELSE 0 END) as unclaimed_count,
    SUM(CASE WHEN status = 'claimed' THEN 1 ELSE 0 END) as claimed_count,
    COUNT(DISTINCT location_found) as unique_locations,
    SUM(CASE WHEN added_date >= CURDATE() THEN 1 ELSE 0 END) as added_today,
    SUM(CASE WHEN added_date >= DATE_SUB(CURDATE(), INTERVAL 7 DAY) THEN 1 ELSE 0 END) as added_this_week
FROM properties
WHERE is_active = TRUE;

-- ============================================
-- 8. FUNCTIONS AND PROCEDURES
-- ============================================

DELIMITER //

-- Function to search properties
CREATE FUNCTION search_properties(
    p_search_term VARCHAR(255),
    p_category VARCHAR(20),
    p_status VARCHAR(20)
)
RETURNS JSON
DETERMINISTIC
BEGIN
    DECLARE result JSON;
    
    SELECT JSON_ARRAYAGG(
        JSON_OBJECT(
            'id', id,
            'type', property_type,
            'category', category,
            'description', description,
            'location', location_found,
            'date', found_date,
            'status', status,
            'image', image_url
        )
    ) INTO result
    FROM properties
    WHERE is_active = TRUE
        AND (p_search_term IS NULL OR 
             description LIKE CONCAT('%', p_search_term, '%') OR
             property_type LIKE CONCAT('%', p_search_term, '%') OR
             location_found LIKE CONCAT('%', p_search_term, '%') OR
             (category = 'id' AND owner_name LIKE CONCAT('%', p_search_term, '%')))
        AND (p_category IS NULL OR category = p_category)
        AND (p_status IS NULL OR status = p_status)
    ORDER BY added_date DESC;
    
    RETURN COALESCE(result, '[]');
END //

-- Procedure to claim a property
CREATE PROCEDURE claim_property(
    IN p_property_id INT,
    IN p_claimant_name VARCHAR(255),
    IN p_claimant_email VARCHAR(255),
    IN p_claimant_phone VARCHAR(50),
    IN p_claimant_registration VARCHAR(100),
    IN p_claimant_message TEXT,
    IN p_proof_details TEXT
)
BEGIN
    DECLARE EXIT HANDLER FOR SQLEXCEPTION
    BEGIN
        ROLLBACK;
        RESIGNAL;
    END;
    
    START TRANSACTION;
    
    -- Check if property exists and is unclaimed
    IF NOT EXISTS (SELECT 1 FROM properties WHERE id = p_property_id AND status = 'unclaimed' AND is_active = TRUE) THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Property not available for claim';
    END IF;
    
    -- Insert claim
    INSERT INTO property_claims (
        property_id, claimant_name, claimant_email, claimant_phone,
        claimant_registration, claimant_message, proof_details, status
    ) VALUES (
        p_property_id, p_claimant_name, p_claimant_email, p_claimant_phone,
        p_claimant_registration, p_claimant_message, p_proof_details, 'pending'
    );
    
    -- Get the claim ID
    SET @claim_id = LAST_INSERT_ID();
    
    -- Log activity
    INSERT INTO property_activity_logs (property_id, action, action_by, user_name, user_email)
    VALUES (p_property_id, 'claim_submitted', 'claimant', p_claimant_name, p_claimant_email);
    
    COMMIT;
    
    SELECT @claim_id as claim_id;
END //

-- Procedure to approve a claim
CREATE PROCEDURE approve_claim(
    IN p_claim_id INT,
    IN p_admin_id INT,
    IN p_admin_notes TEXT
)
BEGIN
    DECLARE v_property_id INT;
    DECLARE v_claimant_name VARCHAR(255);
    DECLARE EXIT HANDLER FOR SQLEXCEPTION
    BEGIN
        ROLLBACK;
        RESIGNAL;
    END;
    
    START TRANSACTION;
    
    -- Get claim details
    SELECT property_id, claimant_name INTO v_property_id, v_claimant_name
    FROM property_claims WHERE id = p_claim_id AND status = 'pending';
    
    IF v_property_id IS NULL THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Claim not found or already processed';
    END IF;
    
    -- Update claim status
    UPDATE property_claims 
    SET status = 'approved',
        admin_notes = p_admin_notes,
        processed_by = p_admin_id,
        processed_date = NOW()
    WHERE id = p_claim_id;
    
    -- Update property status
    UPDATE properties 
    SET status = 'claimed',
        last_updated = NOW()
    WHERE id = v_property_id;
    
    -- Create notification for claimant
    INSERT INTO property_notifications (
        property_id, notification_type, title, message,
        target_audience, target_email
    ) VALUES (
        v_property_id,
        'claim_approved',
        'Claim Approved!',
        CONCAT('Your claim for property has been approved. Please contact the admin to collect your item.'),
        'claimant',
        (SELECT claimant_email FROM property_claims WHERE id = p_claim_id)
    );
    
    -- Log activity
    INSERT INTO property_activity_logs (property_id, action, action_by, user_name, new_status)
    VALUES (v_property_id, 'claim_approved', 'admin', v_claimant_name, 'approved');
    
    COMMIT;
END //

-- Procedure to reject a claim
CREATE PROCEDURE reject_claim(
    IN p_claim_id INT,
    IN p_admin_id INT,
    IN p_admin_notes TEXT
)
BEGIN
    DECLARE v_property_id INT;
    DECLARE v_claimant_name VARCHAR(255);
    DECLARE EXIT HANDLER FOR SQLEXCEPTION
    BEGIN
        ROLLBACK;
        RESIGNAL;
    END;
    
    START TRANSACTION;
    
    -- Get claim details
    SELECT property_id, claimant_name INTO v_property_id, v_claimant_name
    FROM property_claims WHERE id = p_claim_id AND status = 'pending';
    
    IF v_property_id IS NULL THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Claim not found or already processed';
    END IF;
    
    -- Update claim status
    UPDATE property_claims 
    SET status = 'rejected',
        admin_notes = p_admin_notes,
        processed_by = p_admin_id,
        processed_date = NOW()
    WHERE id = p_claim_id;
    
    -- Create notification for claimant
    INSERT INTO property_notifications (
        property_id, notification_type, title, message,
        target_audience, target_email
    ) VALUES (
        v_property_id,
        'claim_rejected',
        'Claim Update',
        CONCAT('Your claim has been reviewed. ', p_admin_notes),
        'claimant',
        (SELECT claimant_email FROM property_claims WHERE id = p_claim_id)
    );
    
    -- Log activity
    INSERT INTO property_activity_logs (property_id, action, action_by, user_name, new_status)
    VALUES (v_property_id, 'claim_rejected', 'admin', v_claimant_name, 'rejected');
    
    COMMIT;
END //

-- Procedure to delete property with audit
CREATE PROCEDURE delete_property(
    IN p_property_id INT,
    IN p_admin_name VARCHAR(255)
)
BEGIN
    DECLARE EXIT HANDLER FOR SQLEXCEPTION
    BEGIN
        ROLLBACK;
        RESIGNAL;
    END;
    
    START TRANSACTION;
    
    -- Log deletion before actual delete
    INSERT INTO property_activity_logs (property_id, action, action_by, user_name, old_status)
    SELECT id, 'deleted', 'admin', p_admin_name, status
    FROM properties WHERE id = p_property_id;
    
    -- Soft delete (set inactive)
    UPDATE properties 
    SET is_active = FALSE, 
        status = 'archived',
        last_updated = NOW()
    WHERE id = p_property_id;
    
    COMMIT;
END //

-- Procedure to get dashboard statistics
CREATE PROCEDURE get_dashboard_stats()
BEGIN
    SELECT * FROM v_dashboard_stats;
END //

DELIMITER ;

-- ============================================
-- 9. EVENTS FOR AUTOMATIC CLEANUP
-- ============================================

-- Event to clean up old notifications (keep for 30 days)
CREATE EVENT IF NOT EXISTS evt_clean_old_notifications
ON SCHEDULE EVERY 1 DAY
DO
BEGIN
    DELETE FROM property_notifications 
    WHERE created_at < DATE_SUB(NOW(), INTERVAL 30 DAY)
        AND is_read = TRUE;
END //

-- Event to archive old properties (after 6 months of being claimed)
CREATE EVENT IF NOT EXISTS evt_archive_old_properties
ON SCHEDULE EVERY 1 WEEK
DO
BEGIN
    UPDATE properties 
    SET status = 'archived',
        is_active = FALSE
    WHERE status = 'claimed' 
        AND last_updated < DATE_SUB(NOW(), INTERVAL 6 MONTH);
END //

-- ============================================
-- 10. INSERT SAMPLE DATA
-- ============================================

-- Insert sample properties
INSERT INTO properties (
    category, property_type, image_url, owner_name, registration_number,
    description, location_found, found_date, status, finder_name, finder_contact
) VALUES
('id', 'Student ID Card', 'https://images.unsplash.com/photo-1589829545856-d10d557cf95f?w=400&h=300&fit=crop', 
 'John Doe', 'REG2023001', 'University ID card with blue background', 
 'Main Library, 2nd Floor', '2026-03-25', 'unclaimed', 'Jane Smith', 'jane@email.com'),
 
('other', 'Backpack', 'https://images.unsplash.com/photo-1545235617-9465d2a55698?w=400&h=300&fit=crop',
 NULL, NULL, 'Black laptop backpack with red zipper, contains notebooks and water bottle',
 'Cafeteria, Near window seats', '2026-03-24', 'unclaimed', 'Security Desk', 'security@campus.com'),
 
('id', 'Employee ID Card', 'https://images.unsplash.com/photo-1611944212121-9c5fa5c33d42?w=400&h=300&fit=crop',
 'Jane Smith', 'EMP2024005', 'Corporate ID with access card',
 'Parking Lot B, Near Entrance', '2026-03-23', 'claimed', 'Mike Johnson', 'mike@email.com'),
 
('other', 'Wallet', 'https://images.unsplash.com/photo-1526170375885-4d8ecf77b99f?w=400&h=300&fit=crop',
 NULL, NULL, 'Brown leather wallet containing credit cards and cash',
 'Student Center, Main Hall', '2026-03-22', 'unclaimed', 'Lost & Found Office', 'lostfound@campus.com');

-- Insert sample claims
INSERT INTO property_claims (property_id, claimant_name, claimant_email, claimant_phone, claimant_message, status)
SELECT id, 'Jane Smith', 'jane@email.com', '+1234567890', 'This is my wallet, it has my ID inside', 'pending'
FROM properties WHERE owner_name = 'Jane Smith' LIMIT 1;

-- ============================================
-- 11. VERIFICATION QUERIES
-- ============================================

SELECT '✅ LOST & FOUND PROPERTIES SCHEMA DEPLOYED SUCCESSFULLY!' as Status;
SELECT COUNT(*) as table_count FROM information_schema.tables 
WHERE table_schema = 'mysite' AND table_name LIKE 'property%';
SELECT COUNT(*) as view_count FROM information_schema.tables 
WHERE table_schema = 'mysite' AND table_type = 'VIEW' AND table_name LIKE 'v_%';
SELECT ROUTINE_NAME as procedures FROM information_schema.routines 
WHERE routine_schema = 'mysite' AND routine_type = 'PROCEDURE' AND ROUTINE_NAME LIKE 'get_%' OR ROUTINE_NAME LIKE '%property%';