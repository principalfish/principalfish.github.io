module.exports = function(grunt) {

	grunt.initConfig({
		pkg: grunt.file.readJSON('package.json'),
		sass: {
			dist: {
				files: {
					'stylesheets/index.css' : 'stylesheets/sass/index.scss',
					'stylesheets/electionmaps/electionmaps.css' : 'stylesheets/sass/electionmaps/electionmaps.scss',
					'stylesheets/electionmaps/navbar.css' : "stylesheets/sass/electionmaps/navbar.scss",
					'stylesheets/electionmaps/polltracker/polls.css' : "stylesheets/sass/electionmaps/polltracker/polltracker.scss",
					'stylesheets/electionmaps/brexit/brexit.css' : "stylesheets/sass/electionmaps/brexit/brexit.scss",
					"stylesheets/hearthstone/main.css" : "stylesheets/sass/hearthstone/hearthstone.scss"
				}
			}
		},
		watch: {
			css: {
				files: '**/*.scss',
				tasks: ['sass', "cssmin"]
			},
			scripts: {
					files: ['scripts/**/*.js'],
					tasks : ['concat', 'uglify']
			}
		},

		concat: {
			  options: {
				// define a string to put between each file in the concatenated output
				separator: ';'
			  },

				electionmapsmain: {
					// the files to concatenate
					src: ['scripts/electionmaps/*.js'],
					// the location of the resulting JS file
					dest: 'electionmaps/main.js'
			  },
				electionmapspolltracker: {
					src : ["scripts/electionmaps/polltracker/*.js"],
					dest : "electionmaps/polltracker/polltracker.js"
 				}	,

				electionmapsbrexit : {
					src : ["scripts/electionmaps/brexit/*js"],
					dest : "electionmaps/brexit/brexit.js"
				},

				hearthstone : {
					src : ["scripts/hearthstone/*.js"],
					dest : "hearthstone/main.js"
				}

		},

		uglify: {
		  options: {
			// the banner is inserted at the top of the output
				banner: '/*! <%= pkg.name %> <%= grunt.template.today("dd-mm-yyyy") %> */\n'
		  },
		  dist: {
				files: {
				  "electionmaps/main.min.js": ["electionmaps/main.js"],
					"electionmaps/polltracker/polltracker.min.js": ["electionmaps/polltracker/polltracker.js"],
					"electionmaps/brexit/brexit.min.js" : ["electionmaps/brexit/brexit.js"],
					"hearthstone/main.min.js" : ["hearthstone/main.js"]
				}
		  }
		},

		cssmin: {
		  electionmapsmain: {
		    src: 'stylesheets/electionmaps/electionmaps.css',
		    dest: 'stylesheets/electionmaps/electionmaps.min.css'
		  },
			navbar:{
				src : "stylesheets/electionmaps/navbar.css",
				dest: "stylesheets/electionmaps/navbar.min.css"
			},
			electionmapspolltracker : {
				src : "stylesheets/electionmaps/polltracker/polls.css",
				dest : "stylesheets/electionmaps/polltracker/polls.min.css"
			},
			electionmapsbrexit : {
				src : "stylesheets/electionmaps/brexit/brexit.css",
				dest : "stylesheets/electionmaps/brexit/brexit.min.css"
			},
			hearthstone : {
				src : "stylesheets/hearthstone/main.css",
				dest : "stylesheets/hearthstone/main.min.css"
			}
		}

	});
	grunt.loadNpmTasks('grunt-contrib-sass');
	grunt.loadNpmTasks('grunt-contrib-watch');
	grunt.loadNpmTasks('grunt-contrib-concat');
	grunt.loadNpmTasks('grunt-contrib-uglify');
	grunt.loadNpmTasks('grunt-contrib-cssmin');
	grunt.registerTask('default',['sass', 'watch']);
}
