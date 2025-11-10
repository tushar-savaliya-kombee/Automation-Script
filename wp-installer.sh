#!/bin/sh
#
# ETS (I) wordpress install script
#
# Dependencies:
# 1: WP-CLI
# 2: Bash
##
clear

# Set up the shell variables for colors
# http://stackoverflow.com/questions/5947742/how-to-change-the-output-color-of-echo-in-linux
yellow=`tput setaf 3`;
green=`tput setaf 2`;
clear=`tput sgr0`;

wpuser="itomic"
admin_email="contact@easternts.com"
table_prefix="etsiwp_"

# Start it up ...
echo "${green}"
echo "============================================"
echo ""
echo "Begin install"
echo ""
echo "============================================"
echo "${clear}"

# accept the url for this site
echo "${yellow}Uri for this site not including http eg (dev.website.com):${clear}"
read -e urlpath
echo ""

# accept the name of our website
echo "${yellow}Site Name:${clear}"
read -e sitename
echo ""

# accept admin username
#echo "${yellow}Admin Username:${clear}"
#read -e wpuser
#echo ""

# accept admin password
echo "${yellow}Admin Password:${clear}"
read -e wppass
echo ""

# accept admin email
#echo "${yellow}Admin email:${clear}"
#read -e admin_email
#echo ""

# accept user input for the databse name
echo "${yellow}Database Name (this will create the db if it does not exist):${clear}"
read -e dbname
echo ""

# accept user input for the database user
#echo "${yellow}Database User:${clear}"
#read -e dbuser
#echo ""

# accept user input for the database password
#echo "${yellow}Database Password:${clear}"
#read -e dbpass
#echo ""

# accept a comma separated list of pages
echo "Add Pages (separate with comma, do not include Home or Blog): "
read -e allpages
echo ""

# accept a comma separated list of posts
echo "Add Posts (separate with comma): "
read -e allposts
echo ""

# install itomic starter theme?
echo "${yellow}Install Itomic Starter Theme? (y/n)${clear}"
read -e starter
echo ""

# add a simple yes/no confirmation before we proceed
echo "${yellow}Run Install? (y/n)${clear}"
read -e run
echo ""

# if the user didn't say no, then go ahead an install
if [ "$run" == n ] ; then
	exit
fi

clear
echo ""

# download the WordPress core files
wp core download

# create the wp-config file with our standard setup
wp core config --dbname=$dbname --dbprefix=$table_prefix --dbuser=root --dbpass=root --extra-php <<PHP
define( 'DISALLOW_FILE_EDIT', true );
define( 'WP_DEBUG', false );
PHP

# parse the current directory name
currentdirectory=${PWD##*/}

# create database, and install WordPress
wp db create
wp core install --url="http://$urlpath" --title="$sitename" --admin_user="$wpuser" --admin_password="$wppass" --admin_email="$admin_email"

# delete sample page
wp post delete $(wp post list --post_type=page --posts_per_page=1 --post_status=publish --pagename="sample-page" --format=ids)
# empty pages in trash
wp post delete $(wp post list --post_type='page' --post_status=trash --format=ids)

# Delete sample post
wp post delete 1 --force

# create home page
wp post create --post_type=page --post_title=Home --post_status=publish --post_author=$(wp user get $wpuser --field=ID)

# create blog page
wp post create --post_type=page --post_title=Blog --post_status=publish --post_author=$(wp user get $wpuser --field=ID)

# set option for front page displays to static page
wp option update show_on_front 'page'

# set home page to be page on front
wp option update page_on_front $(wp post list --post_type=page --post_status=publish --posts_per_page=1 --pagename=home --format=ids)

# set blog page to be page for posts
wp option update page_for_posts $(wp post list --post_type=page --post_status=publish --posts_per_page=1 --pagename=blog --format=ids)

# create pages
export IFS=","
for page in $allpages; do
	wp post create --post_type=page --post_status=publish --post_author=$(wp user get $wpuser --field=ID) --post_title="$(echo $page | sed -e 's/^ *//' -e 's/ *$//')"
done
# create posts
export IFS=","
for post in $allposts; do
	wp post create --post_type=post --post_status=publish --post_author=$(wp user get $wpuser --field=ID) --post_title="$(echo $post | sed -e 's/^ *//' -e 's/ *$//')"
done

# setting the default options
wp option update posts_per_page '1'
wp option update image_default_align 'right'
wp option update image_default_size 'medium'
wp option update image_default_link_type 'none'
wp option update timezone_string 'Australia/Perth'
wp option update default_pingback_flag '0'
wp option update default_ping_status 'closed'
wp option update default_comment_status 'closed'
wp option update comments_notify '0'
wp option update moderation_notify '0'
wp option update blogdescription ''


# set pretty urls
wp rewrite structure '/%postname%/'
wp rewrite flush

# delete hello dolly
wp plugin delete hello

# delete akismet
wp plugin delete akismet

# Install ACF
# get plugin path
acf_zip_file="$(wp plugin path)/acf-pro.zip"
# set acf key
# acf_key="b3JkZXJfaWQ9MzY2NzF8dHlwZT1kZXZlbG9wZXJ8ZGF0ZT0yMDE0LTA4LTA2IDA5OjIzOjI5"
acf_key=""

# get acf zip file
wget -O ${acf_zip_file} "http://connect.advancedcustomfields.com/index.php?p=pro&a=download&k=$acf_key"
# install acf
wp plugin install ${acf_zip_file}
# activate acf
wp plugin activate advanced-custom-fields-pro

# remove zip file
rm ${acf_zip_file}

# run acf register
# wp eval "acf_pro_update_license('$acf_key');"

# Install Gravity forms
wp plugin install http://build.itomic.com.au/vendor/gravityforms.zip --activate

# Install Classic Editor forms
#wp plugin install classic-editor --activate

# Install Broken Link Checker
wp plugin install broken-link-checker --activate

# Install WPS Hide Login
wp plugin install wps-hide-login --activate

# Install Itomic Submenu
#wp plugin install http://build.itomic.com.au/vendor/itomic_submenu.zip --activate

# Install Easy Fancybox
wp plugin install easy-fancybox --activate

# Install Responsive Video Embeds
wp plugin install responsive-video-embeds --activate

# Install Wordpress SEO
# wp plugin install wordpress-seo --activate

# Install restricted-site-access
# wp plugin install restricted-site-access

# Install wordfence
wp plugin install wordfence

# Install MainWP
wp plugin install mainwp-child
wp plugin install mainwp-child-reports


# create a navigation bar
wp menu create "Primary Navigation"

# install starter theme or not
if [ "$starter" == y ] ; then
	wp theme install http://build.itomic.com.au/themes/wordpress-starter-theme.zip --activate
	# assign menu
	wp menu location assign primary-navigation primary-navigation
	echo "add_filter('use_block_editor_for_post', '__return_false', 10);" >> "$(wp theme path)/wordpress-starter-theme/functions.php"
fi


# add pages to navigation, set them to default template (if not done they appear to have no template in backend)
export IFS=" "
for pageid in $(wp post list --order="ASC" --orderby="date" --post_type=page --post_status=publish --posts_per_page=-1 --field=ID --format=ids); do
	wp menu item add-post main-menu $pageid
	wp post meta set $pageid _wp_page_template default
done


# delete built in themes
wp theme delete twentyfifteen
wp theme delete twentysixteen
wp theme delete twentyseventeen
wp theme delete twentynineteen
wp theme delete twentytwenty
wp theme delete twentytwentyone

curl https://bitbucket.org/!api/2.0/snippets/itomic/yrb6/6f1a59652f6abfb555ec2cee786e62ced7082b51/files/wordpress.gitignore > .gitignore
curl https://bitbucket.org/!api/2.0/snippets/itomic/X8zp9p/e9aa8cdeacaf5b73003acd52d41d1f66f6f734d5/files/google07b14c09b8b56e7d.html > google07b14c09b8b56e7d.html

clear

echo "${green}"
echo "================================================================="
echo ""
echo "Installation is complete. :)"
echo ""
echo "================================================================="
echo "${clear}"