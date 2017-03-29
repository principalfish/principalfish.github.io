module.exports = function(grunt) {

	grunt.initConfig({
		pkg: grunt.file.readJSON('package.json'),
		sass: {
			dist: {
				files: {
					'stylesheets/index.css' : 'stylesheets/sass/index.scss',
					'stylesheets/electionmaps/electionmaps.css' : 'stylesheets/sass/electionmaps.scss',
					'stylesheets/electionmaps/navbar.css' : "stylesheets/sass/navbar.scss",
					'stylesheets/electionmaps/polltracker/polls.css' : "stylesheets/sass/polltracker/polltracker.scss",
					'stylesheets/electionmaps/brexit/brexit.css' : "stylesheets/sass/brexit/brexit.scss"
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

				dist1: {
					// the files to concatenate
					src: ['scripts/electionmaps/*.js'],
					// the location of the resulting JS file
					dest: 'electionmaps/main.js'
			  },
				dist2: {
					src : ["scripts/electionmaps/polltracker/*.js"],
					dest : "electionmaps/polltracker/polltracker.js"
 				}	,

				dist3 : {
					src : ["scripts/electionmaps/brexit/*js"],
					dest : "electionmaps/brexit/brexit.js"
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
					"electionmaps/brexit/brexit.min.js" : ["electionmaps/brexit/brexit.js"]
				}
		  }
		},

		cssmin: {
		  my_target: {
		    src: 'stylesheets/electionmaps/electionmaps.css',
		    dest: 'stylesheets/electionmaps/electionmaps.min.css'
		  },
			my_target2:{
				src : "stylesheets/electionmaps/navbar.css",
				dest: "stylesheets/electionmaps/navbar.min.css"
			},
			my_target3 : {
				src : "stylesheets/electionmaps/polltracker/polls.css",
				dest : "stylesheets/electionmaps/polltracker/polls.min.css"
			},
			my_target4 : {
				src : "stylesheets/electionmaps/brexit/brexit.css",
				dest : "stylesheets/electionmaps/brexit/brexit.min.css"
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
